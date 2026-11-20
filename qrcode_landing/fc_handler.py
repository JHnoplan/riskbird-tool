# -*- coding: utf-8 -*-
"""阿里云函数计算 HTTP 函数：微信群活码 302 跳转 + 手机 H5 换码页。

路由：
  GET  / 或 /join              -> 302 跳到当前群邀请链接（横幅二维码指向这里，图案永不变）
  GET  /admin                  -> 手机上传页（选群码图自动解码并更新，无码则提示）
  POST /admin/upload?token=xxx -> 校验口令后写入 join_url.txt 与 qr.png

存储：新桶 heylulan-qr-2026（公共读）
  join_url.txt 当前群邀请链接（302 目标）
  qr.png       当前群码卡片图（跳转失败时的长按兜底）
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.request
from email.utils import formatdate

AK = os.getenv('OSS_AK_ID', '')
SK = os.getenv('OSS_SK', '')
TOKEN = os.getenv('UPLOAD_TOKEN', '')
BUCKET = 'heylulan-qr-2026'
EP = 'oss-cn-shenzhen.aliyuncs.com'
BASE = 'https://%s.%s' % (BUCKET, EP)
JOIN_KEY = 'join_url.txt'
QR_KEY = 'qr.png'


def _put(key, body, ct):
    gmt = formatdate(usegmt=True)
    sts = 'PUT\n\n%s\n%s\n/%s/%s' % (ct, gmt, BUCKET, key)
    sig = base64.b64encode(hmac.new(SK.encode(), sts.encode(), hashlib.sha1).digest()).decode()
    req = urllib.request.Request('%s/%s' % (BASE, key), data=body, method='PUT',
                                 headers={'Date': gmt, 'Content-Type': ct,
                                          'Authorization': 'OSS %s:%s' % (AK, sig),
                                          'Cache-Control': 'max-age=120'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def _get_text(key):
    with urllib.request.urlopen('%s/%s' % (BASE, key), timeout=10) as r:
        return r.read().decode('utf-8').strip()


def _resp(start_response, code, ctype, text):
    start_response(code, [('Content-Type', ctype), ('Cache-Control', 'no-store')])
    return [text.encode('utf-8')]


FALLBACK_HTML = ('<!DOCTYPE html><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<body style="margin:0;font-family:-apple-system,PingFang SC,sans-serif;'
                 'background:#f2f3f5;text-align:center;padding:36px 16px">'
                 '<p style="color:#333;font-size:17px">长按下方二维码，识别后进群</p>'
                 '<img src="%s/%s" style="width:92%%;max-width:430px">'
                 '</body>' % (BASE, QR_KEY))

ADMIN_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>更新群二维码</title></head>
<body style="margin:0;font-family:-apple-system,PingFang SC,sans-serif;background:#f2f3f5;min-height:100vh;display:flex;align-items:center;justify-content:center">
<div style="background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.08);padding:34px 24px;margin:16px;max-width:420px;width:100%;box-sizing:border-box;text-align:center">
<h1 style="font-size:20px;color:#1aad19;margin:0 0 8px">更新群二维码</h1>
<p id="tip" style="color:#888;font-size:14px;margin:12px 0;line-height:1.6">选择群二维码图片<br>（保存的群码图或截图均可），自动识别更新</p>
<input id="file" type="file" accept="image/*" style="display:none">
<button id="btn" onclick="document.getElementById('file').click()" style="background:#1aad19;color:#fff;border:0;border-radius:10px;padding:14px 36px;font-size:16px">选择图片</button>
<p id="msg" style="font-size:14px;margin-top:16px;color:#333"></p>
</div>
<script>__JSQR_SOURCE__</script>
<script>
var TOKEN = '__TOKEN__';
document.getElementById('file').onchange = function () {
  var f = this.files[0]; if (!f) return;
  var msg = document.getElementById('msg'); msg.textContent = '识别中...';
  var fr = new FileReader();
  fr.onload = function () {
    var im = new Image();
    im.onload = function () {
      var cv = document.createElement('canvas');
      var scale = Math.min(1, 1200 / Math.max(im.width, im.height));
      cv.width = Math.round(im.width * scale); cv.height = Math.round(im.height * scale);
      var cx = cv.getContext('2d'); cx.drawImage(im, 0, 0, cv.width, cv.height);
      var d = cx.getImageData(0, 0, cv.width, cv.height);
      var code = jsQR(d.data, cv.width, cv.height);
      if (!code || code.data.indexOf('https://weixin.qq.com/g/') !== 0) {
        msg.textContent = '这张图里没识别出群二维码，请用保存下来的群码图片重试';
        return;
      }
      var rd = new FileReader();
      rd.onload = function () {
        msg.textContent = '上传中...';
        fetch('/admin/upload?token=' + TOKEN, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: code.data, img: rd.result.split(',')[1] })
        }).then(function (r) { return r.text().then(function (t) { return { s: r.status, t: t }; }); })
          .then(function (r) {
            if (r.s === 200) { msg.textContent = '更新成功！现在扫横幅就是新群'; }
            else { msg.textContent = '更新失败：' + r.t; }
          }).catch(function () { msg.textContent = '网络不通，请稍后再试'; });
      };
      rd.readAsDataURL(f);
    };
    im.src = fr.result;
  };
  fr.readAsDataURL(f);
};
</script></body></html>"""


def handler(environ, start_response):
    method = environ.get('REQUEST_METHOD', 'GET').upper()
    path = environ.get('PATH_INFO', '/') or '/'

    if method == 'GET' and path in ('/', '/join'):
        try:
            target = _get_text(JOIN_KEY)
        except Exception:
            target = ''
        if target.startswith('https://weixin.qq.com/g/'):
            start_response('302 Found', [('Location', target),
                                         ('Cache-Control', 'no-store')])
            return [b'']
        return _resp(start_response, '200 OK', 'text/html; charset=utf-8', FALLBACK_HTML)

    if method == 'GET' and path == '/admin':
        html = ADMIN_TMPL.replace('__JSQR_SOURCE__', open('jsQR.js', encoding='utf-8').read()) \
                         .replace('__TOKEN__', TOKEN)
        return _resp(start_response, '200 OK', 'text/html; charset=utf-8', html)

    if method == 'POST' and path == '/admin/upload':
        qs = environ.get('QUERY_STRING', '')
        token = ''
        for p in qs.split('&'):
            if p.startswith('token='):
                token = p[6:]
        if not TOKEN or token != TOKEN:
            return _resp(start_response, '403 Forbidden', 'text/plain; charset=utf-8', '口令不对')
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
            data = json.loads(environ['wsgi.input'].read(length).decode('utf-8'))
        except Exception:
            return _resp(start_response, '400 Bad Request', 'text/plain; charset=utf-8', '请求体不对')
        url = (data.get('url') or '').strip()
        if not url.startswith('https://weixin.qq.com/g/'):
            return _resp(start_response, '400 Bad Request', 'text/plain; charset=utf-8',
                         '链接不是微信群邀请链接')
        try:
            _put(JOIN_KEY, url.encode('utf-8'), 'text/plain')
            img = data.get('img') or ''
            if img:
                _put(QR_KEY, base64.b64decode(img), 'image/png')
        except Exception as e:
            return _resp(start_response, '500 Internal Server Error',
                         'text/plain; charset=utf-8', '写入失败 %s' % e)
        return _resp(start_response, '200 OK', 'application/json', '{"ok":true}')

    return _resp(start_response, '404 Not Found', 'text/plain; charset=utf-8', 'not found')
