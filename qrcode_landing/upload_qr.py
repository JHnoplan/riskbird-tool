# -*- coding: utf-8 -*-
"""群二维码活码更新工具（横幅二维码配套）。

横幅上印的是一个固定不变的链接（用 make_banner_qr.py 生成一次即可），
本脚本只做一件事：把最新的群二维码做成"长按识别"卡片图，覆盖上传到
公司 OSS 的固定地址。上传成功后约 5 分钟内，扫横幅看到的就是新群码。

用法：
  预览卡片效果（不上传）：python upload_qr.py 群码截图.png --preview
  正式更新线上活码：      python upload_qr.py 群码截图.png

密钥与 qcollect/zz_ossupload.py 同一套，从环境变量读取：
  export OSS_ACCESS_KEY_SECRET=xxx
"""
import base64
import hashlib
import hmac
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from email.utils import formatdate

# 2026-11-20 密钥治理：原来 AK_ID 是 `os.environ.get(..., '真实 ID')`——把凭据硬写成兜底默认值。
# 现在只读环境变量，读不到再走下面那条与 SecretKey 同源的 iOS 常量链；两者都没有就在 main 里明确报错。
AK_ID = os.environ.get('OSS_ACCESS_KEY_ID', '')
AK_SECRET = os.environ.get('OSS_ACCESS_KEY_SECRET', '')
# 兜底：与云展 App 同源密钥，从 iOS 工程常量文件读（不打印、不落盘）。
# 路径由环境变量给：那份 iOS 工程不在本仓库里，写死某台机器的位置 = 换一台就是死路
# （2026-11-20 与 deploy_fc.py 同一刀收口）。
IOS_OSS_M = os.environ.get('IOS_OSS_CONST_M', '')


def _ios_const(names):
    # 从 iOS 工程常量里读一把密钥；没配路径/文件不存在/没匹配到一律返回空串，由调用方决定怎么报错。
    if not IOS_OSS_M:
        return ''
    try:
        text = open(IOS_OSS_M, encoding='utf-8', errors='ignore').read()
    except OSError:
        return ''
    m = re.search(names + r'\s*=\s*@"([^"]+)"', text)
    return m.group(1).strip() if m else ''


if not AK_SECRET:
    AK_SECRET = _ios_const(r'(?:SecretKey|AccessKeySecret|OSSAccessKeySecret)')
if not AK_ID:
    # 工程里 ID 的常量名就叫 AccessKey（第 14 行），与 SecretKey 的正则互不误命中。
    AK_ID = _ios_const(r'(?:AccessKeyId|OSSAccessKeyId|AccessKey)')
BUCKET = 'images6396289'
ENDPOINT = 'oss-cn-shenzhen.aliyuncs.com'
KEY = '0817899624/qrcode_landing/qr.png'
PUBLIC_URL = f'https://{BUCKET}.{ENDPOINT}/{KEY}'

SIZE = 900                 # 卡片宽（像素）
HEIGHT = 1100              # 卡片高（像素）
QR_BOX = 660               # 卡片内二维码区域边长
GREEN = (26, 173, 25)
GRAY = (110, 110, 110)
LIGHT = (165, 165, 165)

FONT_CANDIDATES = [
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/Library/Fonts/Arial Unicode.ttf',
]


def _font(size):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def extract_qr_image(src):
    """得到干净的方形群二维码图：
    装了 opencv 就自动解码并重绘成高清净图（截图也能用）；
    没装就用原图居中方块（适合直接保存的群二维码图片）。"""
    from PIL import Image
    img = Image.open(src)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(open(src, 'rb').read(), dtype=np.uint8),
                           cv2.IMREAD_COLOR)
        if arr is not None:
            data, _, _ = cv2.QRCodeDetector().detectAndDecode(arr)
            if data:
                import qrcode
                q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                                  box_size=30, border=2)
                q.add_data(data)
                q.make(fit=True)
                return q.make_image(fill_color='black', back_color='white').convert('RGB')
    except ImportError:
        pass
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    return img.crop((left, top, left + side, top + side))


def compose_card(src, out_path):
    """把群码合成 900x1100 竖版引导卡片：标题 + 长按提示 + 二维码 + 更新日期"""
    from PIL import Image, ImageDraw
    qr_img = extract_qr_image(src).resize((QR_BOX, QR_BOX), Image.LANCZOS)
    card = Image.new('RGB', (SIZE, HEIGHT), 'white')
    d = ImageDraw.Draw(card)
    cx = SIZE // 2
    d.text((cx, 100), '欢迎加入交流群', font=_font(64), fill=GREEN, anchor='mm')
    d.text((cx, 195), '长按识别下方二维码进群', font=_font(40), fill=GRAY, anchor='mm')
    block_top = 260
    card.paste(qr_img, ((SIZE - QR_BOX) // 2, block_top))
    d.text((cx, block_top + QR_BOX + 65), '若提示二维码已过期，请联系群主更新',
           font=_font(34), fill=LIGHT, anchor='mm')
    d.text((cx, HEIGHT - 55), f'更新于 {date.today().isoformat()}',
           font=_font(30), fill=LIGHT, anchor='mm')
    card.save(out_path)
    return out_path


def oss_put(key, body_path, content_type, cache_control='max-age=300'):
    """OSS v1 头签名 PUT（与 qcollect/zz_ossupload.py 同方案），返回 (状态码, 错误摘要)"""
    gmt = formatdate(usegmt=True)
    sts = f'PUT\n\n{content_type}\n{gmt}\n/{BUCKET}/{key}'
    sig = base64.b64encode(hmac.new(AK_SECRET.encode(), sts.encode(),
                                    hashlib.sha1).digest()).decode()
    url = f'https://{BUCKET}.{ENDPOINT}/{key}'
    r = subprocess.run(['curl', '-sS', '-X', 'PUT', '-m', '60',
                        '-H', f'Date: {gmt}', '-H', f'Content-Type: {content_type}',
                        '-H', f'Cache-Control: {cache_control}',
                        '-H', f'Authorization: OSS {AK_ID}:{sig}',
                        '--data-binary', f'@{body_path}', url, '-w', '\n%{http_code}'],
                       capture_output=True, text=True, timeout=90)
    parts = (r.stdout or '').strip().rsplit('\n', 1)
    code = parts[-1] if parts else '?'
    err = parts[0][:200] if len(parts) > 1 and code != '200' else (r.stderr or '')[:200]
    return code, err


def main():
    args = [a for a in sys.argv[1:] if a != '--preview']
    preview = '--preview' in sys.argv
    if not args:
        print('用法：python upload_qr.py 群码图片.png [--preview]')
        print('  --preview  只在本地生成卡片预览，不上传')
        sys.exit(1)
    src = args[0]
    if not os.path.exists(src):
        print(f'找不到文件：{src}')
        sys.exit(1)

    if preview:
        out = os.path.abspath('card_preview.png')
        compose_card(src, out)
        print(f'卡片已生成：{out}')
        print('打开看一眼效果，没问题就去掉 --preview 正式上传。')
        return

    if not (AK_ID and AK_SECRET):
        print('缺少环境变量 OSS_ACCESS_KEY_SECRET。')
        print('第 1 步：先在终端执行 export OSS_ACCESS_KEY_SECRET=你的密钥')
        print('第 2 步：再运行本命令（与 qcollect 采集上传用的是同一把密钥）')
        sys.exit(1)

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    compose_card(src, tmp.name)
    code, err = oss_put(KEY, tmp.name, 'image/png')
    os.remove(tmp.name)
    if code == '200':
        print(f'更新成功。活码地址（横幅上印的就是它）：{PUBLIC_URL}')
        print('约 5 分钟内，扫横幅看到的就是新群码。')
    else:
        print(f'上传失败（HTTP {code}）：{err}')
        print('多为密钥不对或没权限，检查 OSS_ACCESS_KEY_SECRET 后重试。')
        sys.exit(1)


if __name__ == '__main__':
    main()
