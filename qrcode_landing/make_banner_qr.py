# -*- coding: utf-8 -*-
"""横幅二维码生成器：把活码固定链接生成为高清二维码 PNG。

只需运行一次，得到的二维码图案永久不变，可以直接交给广告公司印横幅：

    python make_banner_qr.py

自定义尺寸或地址：
    python make_banner_qr.py --url https://... --box 60 -o banner_qr.png

印刷建议：二维码边长 >= 扫描距离 / 10（3 米外扫至少 30cm），
四周留白，深码浅底，别覆反光膜。
"""
import argparse

try:
    import qrcode
except ImportError:
    print('先安装依赖：.venv/bin/pip install qrcode')
    raise SystemExit(1)

from upload_qr import PUBLIC_URL


def main():
    p = argparse.ArgumentParser(description='活码固定链接 → 横幅高清二维码')
    p.add_argument('--url', default=PUBLIC_URL, help='活码地址（默认已内置线上地址）')
    p.add_argument('-o', '--output', default='banner_qr.png')
    p.add_argument('--box', type=int, default=40, help='单个码点像素，默认40（横幅够用）')
    a = p.parse_args()
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=a.box, border=4)
    qr.add_data(a.url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    img.save(a.output)
    print(f'已生成：{a.output}（{img.size[0]}x{img.size[1]} 像素）')
    print(f'二维码内容：{a.url}')
    print('此图案永久有效，印刷前记得先扫码实测一次。')


if __name__ == '__main__':
    main()
