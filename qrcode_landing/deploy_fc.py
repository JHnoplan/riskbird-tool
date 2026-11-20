# -*- coding: utf-8 -*-
"""一键部署/更新 阿里云函数计算活码服务（幂等，可反复运行）。

    python deploy_fc.py

完成后打印两个地址：
  横幅地址（印横幅用的固定链接，make_banner_qr.py 默认用它）
  管理页地址（手机打开，选图上传换群码；不要外传）
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import tempfile
import urllib.request
from email.utils import formatdate

import fc2

HERE = os.path.dirname(os.path.abspath(__file__))
# 兜底密钥来源：与云展 App 同源的 iOS 常量文件。路径由环境变量给，**不写死本机位置**
# （2026-11-20 收口：原来这一行是 `/Users/<开发机用户>/…`，而那份 iOS 工程本来就不在
# 本仓库里——换一台机器它就是一条永远读不到的死路，报错话里还指着它）。
IOS_M = os.environ.get('IOS_OSS_CONST_M', '')
AK_ID = os.environ.get('OSS_ACCESS_KEY_ID', '')  # 2026-11-20 密钥治理：真凭据不许再当兜底默认值写进源码
REGION = 'cn-shenzhen'
SVC, FN = 'qr-live', 'qr-join'


def _ios_const(names):
    # 从 iOS 工程常量里读一把密钥；没配路径/文件不存在/没匹配到都返回空串，由调用方决定怎么报错。
    # 原来这里直接 open()：换台机器没有这个工程就是 FileNotFoundError 堆栈，属于"报错没头没尾"那一族。
    if not IOS_M:
        return ''
    try:
        text = open(IOS_M, encoding='utf-8', errors='ignore').read()
    except OSError:
        return ''
    m = re.search(names + r'\s*=\s*@"([^"]+)"', text)
    return m.group(1).strip() if m else ''


def _secret():
    sk = (os.environ.get('OSS_ACCESS_KEY_SECRET', '').strip()
          or _ios_const(r'(?:SecretKey|AccessKeySecret|OSSAccessKeySecret)'))
    if not sk:
        raise SystemExit('缺少 AccessKeySecret：请 export OSS_ACCESS_KEY_SECRET，'
                         '或 export IOS_OSS_CONST_M=<AliyunOSSUpload.m 的绝对路径>')
    return sk


def _ak_id():
    ak = (os.environ.get('OSS_ACCESS_KEY_ID', '').strip()
          or _ios_const(r'(?:AccessKeyId|OSSAccessKeyId|AccessKey)'))
    if not ak:
        raise SystemExit('缺少 AccessKeyId：请 export OSS_ACCESS_KEY_ID，'
                         '或 export IOS_OSS_CONST_M=<AliyunOSSUpload.m 的绝对路径>')
    return ak


def _account_id(secret):
    gmt = formatdate(usegmt=True)
    sts = 'GET\n\n\n%s\n/' % gmt
    sig = base64.b64encode(hmac.new(secret.encode(), sts.encode(), hashlib.sha1).digest()).decode()
    req = urllib.request.Request('https://oss-cn-shenzhen.aliyuncs.com/',
                                 headers={'Date': gmt, 'Authorization': 'OSS %s:%s' % (AK_ID, sig)})
    xml = urllib.request.urlopen(req, timeout=20).read().decode('utf-8')
    m = re.search(r'<Owner>.*?<ID>(\d+)</ID>', xml, re.S)
    if not m:
        raise SystemExit('取账号 ID 失败：%s' % xml[:150])
    return m.group(1)


def _ignore(fn, *a):
    try:
        fn(*a)
    except fc2.FcError:
        pass


def main():
    global AK_ID
    AK_ID = _ak_id()   # 先补齐再往下走：后面 _account_id / fc2.Client / 函数环境变量三处都要用它
    sk = _secret()
    aid = _account_id(sk)
    client = fc2.Client(endpoint='%s.%s.fc.aliyuncs.com' % (aid, REGION),
                        accessKeyID=AK_ID, accessKeySecret=sk)
    try:
        client.get_service(SVC)
        print('服务已存在:', SVC)
    except fc2.FcError as e:
        print('创建服务:', str(e)[:80])
        client.create_service(SVC, description='微信群横幅活码')

    # 口令：本地留存一份，首次自动生成
    token_path = os.path.join(HERE, 'upload_token.txt')
    if os.path.exists(token_path):
        token = open(token_path, encoding='utf-8').read().strip()
    else:
        token = secrets.token_hex(8)
        open(token_path, 'w').write(token)
        os.chmod(token_path, 0o600)

    # 组装代码目录：handler 源码 + 内嵌 jsQR
    jsqr = open('/tmp/jsQR.min.js', encoding='utf-8').read()
    src = open(os.path.join(HERE, 'fc_handler.py'), encoding='utf-8').read()
    src = src.replace("open('jsQR.js', encoding='utf-8').read()", "__JSQR_EMBED__")
    marker = '__JSQR_EMBED__'
    if marker in src:
        src = src.replace(marker, repr(jsqr))
    code_dir = tempfile.mkdtemp(prefix='fc_code_')
    open(os.path.join(code_dir, 'fc_handler.py'), 'w', encoding='utf-8').write(src)

    _ignore(client.delete_trigger, SVC, FN, 'http')
    _ignore(client.delete_function, SVC, FN)
    client.create_function(SVC, FN, runtime='python3.10', handler='fc_handler.handler',
                           codeDir=code_dir, description='横幅活码跳转与换码页',
                           memorySize=256, timeout=60,
                           environmentVariables={'OSS_AK_ID': AK_ID, 'OSS_SK': sk,
                                                 'UPLOAD_TOKEN': token})
    client.create_trigger(SVC, FN, 'http', 'http',
                          {'authType': 'anonymous', 'methods': ['GET', 'POST']},
                          sourceArn=None, invocationRole=None)
    shutil.rmtree(code_dir, ignore_errors=True)

    url = 'https://%s.%s.%s.fcapp.run' % (FN, SVC, REGION)
    open(os.path.join(HERE, 'live_url.txt'), 'w').write(url)
    print('部署完成')
    print('横幅地址（固定，印横幅用）:', url)
    print('管理页（手机打开换群码，勿外传）:', url + '/admin')
    print('上传口令（已自动带入管理页，本地备份在 upload_token.txt）:', token)


if __name__ == '__main__':
    main()
