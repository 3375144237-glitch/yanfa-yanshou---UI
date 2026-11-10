import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

let browser;
try {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const config = JSON.parse(input);
  if (!config.url || !config.viewport?.width || !config.viewport?.height || !config.ready_selector || !config.output) {
    throw new Error('CONFIG_INVALID');
  }
  let chromium;
  try { ({chromium} = await import('playwright')); } catch { throw new Error('PLAYWRIGHT_MISSING'); }
  try { browser = await chromium.launch({headless: config.headless !== false, ...(config.channel ? {channel:config.channel} : {})}); }
  catch { throw new Error('BROWSER_MISSING'); }
  const context = await browser.newContext({viewport:config.viewport, deviceScaleFactor:config.dpr ?? 1,
    locale:config.locale ?? 'zh-CN', timezoneId:config.timezone ?? 'Asia/Shanghai', colorScheme:config.color_scheme ?? 'light',
    reducedMotion:'reduce', ...(config.storage_state ? {storageState:config.storage_state} : {})});
  const page = await context.newPage();
  await page.goto(config.url, {waitUntil:'domcontentloaded', timeout:30000});
  for (const action of config.actions ?? []) {
    if (action.type === 'click') await page.locator(action.selector).click();
    else if (action.type === 'fill') await page.locator(action.selector).fill(action.value);
    else if (action.type === 'press') await page.locator(action.selector).press(action.key);
    else if (action.type === 'scroll') await page.locator(action.selector).scrollIntoViewIfNeeded();
    else throw new Error('CONFIG_INVALID');
  }
  try { await page.locator(config.ready_selector).waitFor({state:'visible', timeout:15000}); }
  catch { throw new Error('PAGE_NOT_READY'); }
  // Freeze CSS motion and wait for image/font loading before measurement and capture.
  await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}'});
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(Array.from(document.images).map(img => img.complete ? Promise.resolve() : new Promise(resolve => {
      img.addEventListener('load',resolve,{once:true}); img.addEventListener('error',resolve,{once:true});
    })));
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  });
  const fullPage = config.full_page === true;
  const capture = () => page.evaluate(({mappings,fullPage}) => {
    const mapped = new Map();
    const queries = {};
    for (const [designId, rule] of Object.entries(mappings)) {
      if (rule.selector) {
        const matches=document.querySelectorAll(rule.selector);
        queries[designId]=matches.length ? (matches.length===1 ? 'exists' : 'ambiguous') : 'absent';
        for (const el of matches) mapped.set(el,[...(mapped.get(el)??[]),designId]);
      }
    }
    const selectorFor = el => {
      if (el.getAttribute('data-ui-review')) {
        const s = `[data-ui-review="${CSS.escape(el.getAttribute('data-ui-review'))}"]`;
        if (document.querySelectorAll(s).length === 1) return s;
      }
      if (el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1) return `#${CSS.escape(el.id)}`;
      const parts=[];
      for (let n=el; n && n!==document.body; n=n.parentElement) {
        const siblings=Array.from(n.parentElement?.children??[]).filter(x=>x.tagName===n.tagName);
        parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(n)+1})`);
      }
      return ['body',...parts].join(' > ');
    };
    const color = value => {
      const match=value.match(/^rgba?\(([^)]+)\)$/);
      if (!match) return undefined;
      const nums=match[1].split(',').map(Number);
      if (nums.length<3 || nums.some(v=>!Number.isFinite(v))) return undefined;
      return '#'+[...nums.slice(0,3),Math.round((nums[3]??1)*255)].map(v=>Math.round(v).toString(16).padStart(2,'0')).join('');
    };
    const viewport={width:innerWidth,height:fullPage?Math.max(document.documentElement.scrollHeight,innerHeight):innerHeight};
    const elements=[];
    for (const el of document.body.querySelectorAll('*')) {
      if (['SCRIPT','STYLE','NOSCRIPT','META','LINK'].includes(el.tagName)) continue;
      const r=el.getBoundingClientRect(), s=getComputedStyle(el);
      const x=r.x+(fullPage?scrollX:0), y=r.y+(fullPage?scrollY:0);
      if (r.width<=0 || r.height<=0 || s.display==='none' || s.visibility==='hidden' || Number(s.opacity)===0) continue;
      let transparentAncestor=false;
      for(let parent=el.parentElement;parent;parent=parent.parentElement) {
        if(Number(getComputedStyle(parent).opacity)===0) {transparentAncestor=true;break;}
      }
      if(transparentAncestor) continue;
      if (x>=viewport.width || y>=viewport.height || x+r.width<=0 || y+r.height<=0) continue;
      const selector=selectorFor(el);
      const ownText=Array.from(el.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
      const text=el.tagName==='INPUT' ? null : ownText || null;
      const style={font_family:s.fontFamily.split(',')[0].trim().replace(/^['"]|['"]$/g,'')};
      const attrs={fontSize:'font_size',fontWeight:'font_weight',lineHeight:'line_height',letterSpacing:'letter_spacing',
        borderTopWidth:'border_width',paddingTop:'padding_top',paddingRight:'padding_right',paddingBottom:'padding_bottom',paddingLeft:'padding_left',gap:'gap'};
      for (const [key,prop] of Object.entries(attrs)) {
        const value=parseFloat(s[key]); if (Number.isFinite(value)) style[prop]=value;
      }
      if ([s.borderTopLeftRadius,s.borderTopRightRadius,s.borderBottomLeftRadius,s.borderBottomRightRadius].every(v=>v===s.borderTopLeftRadius) && /^\d+(\.\d+)?px$/.test(s.borderTopLeftRadius)) style.border_radius=parseFloat(s.borderTopLeftRadius);
      for (const [key,prop] of [['color','color'],['backgroundColor','background_color']]) {
        const value=color(s[key]); if(value) style[prop]=value;
      }
      const key=el.getAttribute('data-ui-review')||el.id||'';
      elements.push({id:selector,selector,key,name:key||el.getAttribute('aria-label')||text||el.tagName.toLowerCase(),
        type:el.tagName,text,bbox:{x,y,width:r.width,height:r.height},style,mapped_design_ids:mapped.get(el)??[]});
    }
    return {schema_version:1,coordinate_unit:'logical_px',platform:'web',viewport,elements,mapping_queries:queries};
  },{mappings:config.mappings??{},fullPage});
  const before = await capture();
  await fs.mkdir(config.output,{recursive:true});
  await page.screenshot({path:path.join(config.output,'actual-source.png'),fullPage,animations:'disabled'});
  const after = await capture();
  if (JSON.stringify(before)!==JSON.stringify(after)) throw new Error('PAGE_NOT_READY');
  before.environment={browser:browser.version(),dpr:config.dpr??1,locale:config.locale??'zh-CN',
    timezone:config.timezone??'Asia/Shanghai',color_scheme:config.color_scheme??'light',
    viewport:config.viewport,full_page:fullPage,
    page_identity:createHash('sha256').update(JSON.stringify({url:page.url(),ready_selector:config.ready_selector,actions:config.actions??[]})).digest('hex')};
  await fs.writeFile(path.join(config.output,'actual.snapshot.json'),JSON.stringify(before,null,2)+'\n');
} catch (error) {
  const known=['CONFIG_INVALID','PLAYWRIGHT_MISSING','BROWSER_MISSING','PAGE_NOT_READY'];
  process.stderr.write(known.includes(error.message)?error.message:'CAPTURE_FAILED');
  process.exitCode=1;
} finally { if(browser) await browser.close(); }
