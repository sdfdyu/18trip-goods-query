const ALLOW_HOSTS = [
  'movic.jp',
  'canime.jp',
  'animate-onlineshop.jp',
  'techorus-cdn.com',
  '18trip.jp',
  'eeo.jp',
  'sk-japan.co.jp',
  'doubaocdn.com',
  'pbs.twimg.com',
  'st-hatena.com',
  'hatenablog.com'
];

const BROWSER_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
  'Accept-Language': 'ja,en;q=0.9',
  'Sec-Fetch-Dest': 'image',
  'Sec-Fetch-Mode': 'no-cors',
  'Sec-Fetch-Site': 'cross-site'
};

function allowed(hostname) {
  const h = String(hostname || '').toLowerCase();
  return ALLOW_HOSTS.some(function (d) { return h === d || h.endsWith('.' + d); });
}

function text(msg, status) {
  return new Response(msg, {
    status: status || 200,
    headers: { 'Content-Type': 'text/plain; charset=utf-8' }
  });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const raw = url.searchParams.get('u');
  if (!raw) return text('缺少参数 u。用法：/p?u=<encodeURIComponent(图片地址)>');

  let target;
  try { target = new URL(raw); } catch (e) { return text('图片地址无效', 400); }
  if (target.protocol !== 'https:' && target.protocol !== 'http:') return text('协议不支持', 400);
  if (!allowed(target.hostname)) return text('域名不在白名单: ' + target.hostname, 403);

  const cache = caches.default;
  const cacheKey = new Request(url.toString(), { method: 'GET' });
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  let upstream;
  try {
    upstream = await fetch(target.toString(), {
      method: 'GET',
      headers: Object.assign({ 'Referer': target.origin + '/' }, BROWSER_HEADERS),
      redirect: 'follow'
    });
  } catch (e) {
    return text('回源失败: ' + (e && e.message ? e.message : 'unknown'), 502);
  }

  if (!upstream.ok) return text('源站返回 ' + upstream.status, 502);

  const ctype = upstream.headers.get('Content-Type') || '';
  if (ctype && ctype.indexOf('image/') !== 0) return text('源站返回的不是图片: ' + ctype, 415);

  const headers = new Headers();
  headers.set('Content-Type', ctype || 'image/jpeg');
  headers.set('Cache-Control', 'public, max-age=31536000, immutable');
  headers.set('Access-Control-Allow-Origin', '*');

  const res = new Response(upstream.body, { status: 200, headers });
  try { await cache.put(cacheKey, res.clone()); } catch (e) { }
  return res;
}
