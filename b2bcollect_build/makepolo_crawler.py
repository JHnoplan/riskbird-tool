"""makepolo_crawler.py —— 马可波罗网(cn.makepolo.com) 关键词搜索采集器（纯 HTTP 版）。

2026-11-04 实机探测校准。这个站的价值是库大（「制冷设备」一个词就 1,429,811 个产品），
难点是**手机号不给明文**：渲染成一张 PNG（`/purchase/buildStringToimg.php?t_page=<通道>&phone=<密文token>`）。
本采集器把「号码图片码」当作一条正式通路打通，识别引擎见 mk_phone_ocr.py（纯 Pillow 模板匹配，
不联网、不装任何 OCR 库；189 张真机样本上 173 张出字且形态合规，人工转写的 20 张真值上逐位核对通过）。

实测要点（都是踩出来的，不是猜的）：
  ✅ 列表：GET `http://caigou.makepolo.com/spc_new.php?search_flag=2&q=<GBK>&pg=<N>`，
     必须带 Referer（否则薄壳），页体 181KB；**编码是 gbk/gb18030**。
     每页 40 张产品卡，卡内 `[广东东莞]` + 公司名 + `http://<slug>.cn.makepolo.com/` +
     `contact_us.html?proid=<产品id>`，按 slug 去重后一页约 22 家公司。
  ✅ 详情：`http://<slug>.cn.makepolo.com/contact_us.html`，**编码是 UTF-8**（与搜索域不同，
     按 GBK 解会得到「涓滆帪」这类乱码）。两套模板并存，字段解析必须同时认：
       A 型（028pack） `<span>联系人：张先生</span>` / `<span>手机：<img 码></span>` /
                      `<span>Email：13881877244@139.com</span>` / `<span>传真：028-87631203</span>`
       B 型（dgghzl）  `<li>…码图 | …码图</li>` / `<li>传真：0769-83034002</li>` /
                      `<li>公司地址：…</li>` / `<li>Email：…</li>`
     另有一份 SEO 明文在 `<meta content="公司名联系人：X、公司网址：slug.cn.makepolo.com、地址：…、Email…">`。
  ⚠ **传真陷阱**：B 型页面上唯一醒目的明文号码 `0769-83034002` 是「传真」，不是电话。
     按「明文号码 = 电话」粗着抓，会把一批传真机号码当固话入库（外呼打不通）。
     所以：明文传真单独记账，凡是图片码 OCR 出来的固话与明文传真**串一致**就判为传真、丢弃；
     A 型「电话：」那张码的 token 长度（55→12 字符）与明文传真 028-87631203 同为 12 字符，
     正是靠这条差分才看出「有的店把传真当电话挂出来」——入库一律只收形态合规且不等传真的号。
  ⚠ 出图是**按店**的（并有时段波动）：406 家商铺里 51 家（12.6%）会放号码图，且这 51 家里
     有 25 家一放就是 5、6 张；同一家 028pack，采集期出过 6 张图、复核时同样 6 个 token
     全回 0 字节。0 字节时响应是 `text/html; charset=UTF-8` 空正文，不是图片，
     所以「拿回 PNG 魔数」才是成功判据。据此取两阶段策略（常量里带历史数据回测）：
     先探测两张，命中就把这一家其余号码一次取齐；探测不中只再补一张。
     回测结果：召回 98.4%、平均 2.93 请求/店（全打要 4.73）。另带一个很保守的
     站点级熔断（连续 30 家一张都没拿到才停图），因为出图率 12.6% 下 8 家全不中
     是常事——阈值设得太浅会把整轮手机号直接锁死（上一版设 8 就踩过这个坑）。
     自从打通产品详情页明文手机后，图片码降为**回落通路**：只有产品页没给出手机时
     才花这几个取图请求（一店正常只读产品页 + 商铺联系方式页两页，比从前每家省 ~2 个请求）。
  ✅ 手机号主通路（2026-11-04 实测突破，完整跑 43 家：拿到手机的 25 家，剔掉站方已下架的 12 家后是活店 31 家里的 25 家，八成）：联系方式页把手机藏成图片码，
     但**同一个号在它自己的产品详情页上是明文**：`china.makepolo.com/product-detail/<proid>.html`
     的「公司名片」里写着 `<li>手机号：<span>13450460020 </span></li>`，而同一张卡片里就带着
     联系人 + 公司名称 + 本店子域链接（归属自证，实测 14 页子域全对）。列表卡本来就带 proid，
     所以一跳可达。产品页还有「供应商信息」块给 电话/传真/地址（字段名被站方拆成
     `<span>电</span>话` 这种写法防爬，得按原样认）。
  ⚠ 产品页**绝不能全文扫手机号**：`"appid": "1620789529555202"`、统一社会信用代码
     `91441900551720725X`、页内十六进制跟踪串都能凑出 11 位「合法规格手机」，必须按名片块取。
     （连 404 页模板里都挂着两个站方号码 `13748209046`/`18112113105`，实测四家不相干店
     的 404 页一模一样——全文扫号必然把它们当本公司的号。）
  ✅ 覆盖率两处补强（2026-11-04 走完整 crawl() 的小流量实机跑账目，不是单店试跑）：
     ① 早过滤带上**公司名**（`stub_relevance_text`）：本站产品卡的「主营产品」写的是一级
        类目词，与关键词常常不同字。三页 69 张卡实测：只看主营产品只放过 43%，而公司名里
        就带行业词的同行被误杀 17 家（占 25%）——「天津良丰制冷设备有限公司 / 冷却塔」
        「昆山格律斯机械设备制造有限公司 / 冷冻机,冷水机、制冰机」。带上公司名后放过 68%，
        而「伊明传动科技（厦门）/ 减速器」这类真不相关的仍然砍掉（判定是命中即放行，
        多一个字段只会变宽，不会新增误杀）。
     ② 首卡产品页不给看就**换同店其它产品 id**（`_pd_alternates`）：三页 50 家里 24 家（48%）
        本来就带 2 个以上产品 id，备选就在已解析的卡片里，不额外花列表请求；而且产品页与
        商铺子域是两套开关（实测 昆山冠信(slug=15190159322) 联系页 439 而产品页 200 且名片
        手机齐全，反过来也成立），换一条腿就有机会成。只多打 1~2 下，且只在产品页那一跳
        没给手机的店上才花。
     ③ 早过滤的三处补强（2026-11-04 用 debug/mp_filter_audit.py 把「砍掉没进详情」的名单
        整份打出来逐条过目后做的，取证都在那轮数据里）：
        · 同店多张产品卡的**主营并起来判**（parse_list 的「主营全文」，只喂判定、不进记录）：
          实机 深圳市鲁海阳精密机械配件 两张卡一张写「其他机床附件」一张写「通用五金配件」，
          翻页时首卡先被标记已见，只看首卡就把这家误杀。
        · 同族类目桥（cdp_base.is_relevant → keyword_expander.PEER_CATEGORY）：搜「数控机床」
          回出来的车床/铣床/加工中心/电火花线切割机厂与关键词**零二字组重叠**，按现规则全砍。
          实机砍掉名单里这样的真同行有 8 家，补桥后这 8 家全部入库、其中 4 家还带出手机号。
          桥表是窄的人工表（只收实机证明过误杀的行业），「货运代理/滤芯/涂料/减速机」这些
          真不相干的仍照砍——补桥后重新过目放过的那一批，多出来的全是机床/金属加工同行。
        · 砍掉的公司**不再占全局已见**（9 份翻页循环同此一处）：原先是「先按子域/公司名标已见、
          再判相关」，于是它对 A 词被判类目不符后，同一轮里 B 词再也碰不到这一家；而相关性
          本来就是「对某个关键词」的属性。现在只按「关键词#主体」标记，与种子库口径一致。
  ⚠ 439 是**整家商铺在站方下架**而不是限流（取证：实测四家「两页都空」的店，首页/联系页/
     产品列表/产品页全 439，同机同期别家 200，隔十几分钟换会话再打还是 439；而且换 https、
     给联系页加来源参数、改访问它的首页与产品列表都照样不给）——所以这种店给单独状态
     「商铺已关闭(站方不给看)」：既不是「没采到」也不是风控，不白重试，并且它会进种子库
     （重试两次后锁定），不会像「导航失败」那样每轮重新烧两个请求。网络异常仍归
     「详情页导航失败」（下一轮重采）。
  ✅ 三条不需要 OCR 的手机号补充通路（实测都有）：
     ① 商铺子域本身就是手机号（slug=13790516748，OCR 出同一串，双源互证）；
     ② 运营商手机邮箱前缀（Email：13881877244@139.com → 13881877244；139.com/189.cn/wo.cn
        这三个域按号段分配给用户，前缀就是本机号，且页面同时把该串当邮箱登记，属商家自证）；
     ③ 页面明文「电话/手机：」直写数字的少数店。
  ⛔ 平台假值：手机 `17368614555`（站方模板号）、邮箱 `service@makepolo.com` 实测在库里出现，一律剔。
  ✅ 数据质量四处收尾（都出自完整 crawl() 实机跑「数控机床」「制冷设备」两轮，按「宁缺不假」处理）：
     ① 商家把「产品词 + 姓 + 称谓」连写进联系人栏，**前粘后粘都有**（前粘实测「热处理备件杨经理」，
        后粘实测文安县汇永商贸名片写「田经理二手机床」——基类按尾巴上的称谓取名，切成「田经理二」这种
        谁也叫不出口的字）。`clean_contact_person` 按姓氏表只在「姓＋称谓」贴着串首或串尾时切掉产品词，
        切不出来就原样交回；后粘还多一道护栏：串尾接的是「总经理/助理/业务」这类**职务词**就不切
        （「李总经理」切半截成「李总」是把职称切碎）。并列多人「周先生/李女士」取第一位（基类会并成
        「周/李」这种没法叫的）。清洗函数是**定点**的：对已入库的值再跑一次不会变更，离线断言钉住。
     ② 写坏的邮箱（实测 `test@2449181705qq.com`：本名是占位词 test，域名首段又是 10 位数字打头，
        商家把 QQ 号抄进了域名）——`is_bad_email` 直接作废，**绝不猜成 `2449181705@qq.com`**，那是推断
        出来的地址，猜错就把邮件发到陌生人邮箱。这两家本身有手机/固话，作废邮箱不损失联系方式。
     ③ 站方实体写法随手（&nbsp 不带分号、甚至 &amp;nbsp 双重转义）。实测 html.unescape **认得**不带
        分号的具名实体（还原成不间断空格，随空白一起收掉），所以脏值入库的真正原因是**产品页名片那条路当初根本
        没 unescape**（实机联系人「张新鲜&nbsp」）。现在列表公司名/主营、名片联系人、电话传真地址、meta 兜底、
        联系方式页字段全部走同一个 clean_text（先 unescape，再补洗双重转义残体），不再有某条路漏洗的机会。
  ✅ 假区号固话闸门（同一轮实机第 4 家）：手机 19352178592 被商家写成「0193-52178592」挂进固话列。
     这种串形状过得了 PHONE_TEL_RE（0 开头 + 7~8 位本体），但 0193 不在真实长途区号树里
     （只有 010 / 02X / 03xx~09xx 三种），外呼必打不通。`is_valid_landline` 按区号表核对后整串
     剔掉——手机号本来就在自己那一列，剔掉假固话不损失联系方式，也不给用户一列拨不通的号。
  ⛔ **推广位串号陷阱**（差点把别人的号码卖给用户，实机发现）：页面上的码图**不都属于本店**。
     长沙东方工业泵厂(csdfgyb123) 与 智恒厦门微电子(51chinasensor) 的「联 系商家」弹窗
     (phone_mask_list) 文案一字不差（进口配件/进口品牌现货/现货微信同步），其中两个
     识别结果也完全相同：13918838942、18217311176（都是上海号段）——那是 CPC 推广位
     广告主的号码。两家真号（13755154094 长沙 / 15980846919 厦门）与各自的固话区号
     （0731 / 0592）、与店铺所在地市三重一致，而推广号两边都对不上。
     所以取码图必须先做**归属判定**（见 _shop_code_tokens）+ **跨店重复度哨兵**
     （同一号第 3 家店起剔除，见 _public_num_hit），不能页面级 findall 一把抓。
"""
import re
import time
import html as _htmlmod
from urllib.parse import quote, urljoin

import requests

from cdp_base import (CdpSiteCrawler, PHONE_MOBILE_RE, PHONE_TEL_RE, EMAIL_RE, clean_address,
                      parse_contact_field, is_placeholder_number, dept_only, compose_position,
                      strip_country_code, HONORIFIC_ONLY_RE, is_valid_landline)
# 裸 HTTP 超时统一取值（连接/读数拆开，由来见 cdp_base.http_timeout 上方）
from cdp_base import http_timeout
from mk_phone_ocr import load_default_font, expected_chars, TOKEN_LEN_OFFSET
import province_filter as _pf

HOME_URL = "http://cn.makepolo.com/"
SEARCH_URL = "http://caigou.makepolo.com/spc_new.php?search_flag=2&q={kw}&pg={page}"
SHOP_URL = "http://{slug}.cn.makepolo.com/contact_us.html"
# 商铺首页：contact_us 不通时的第二条腿。**注释旧版写着「两页的壳同构，不需要另写判据」——那句是错的**，
# 2026-11-15 实机留档取证：首页是 block2 壳（<span class="block2">手机：</span><span class="block2" …>
# <em class="yellow_left_mobile_cpc"><img src="/purchase/buildStringToimg2.php?…&fei=1&en_corpid=…">），
# 标签带 class、值落在紧接其后的那一个 span 里，码图还是 buildStringToimg**2**.php 那一族。
# 旧 _FIELD_RE/_CONTACT_SCOPE_RE 只认不带属性的 <span>、旧 _CODE_RE 的 `\.php` 又被那个 2 顶开，
# 于是这一跳 200 拿回来却一个字段、一张码图原料都读不出——「号在图里」被记成「这家没联系方式」。
# 现在这两套判据按上面的实机形状都放宽了（放宽只多认形状，不新认语义，contact_us 那两种旧模板原样命中）。
SHOP_HOME_URL = "http://{slug}.cn.makepolo.com/"
SHOP_HOST = "http://{slug}.cn.makepolo.com"
IMG_URL = "http://{slug}.cn.makepolo.com/purchase/buildStringToimg.php?t_page={style}&phone={token}"
# 商铺自己的产品中心页：列表卡那几个产品 id 都没给明文手机时的**第三条取号腿**。
# 2026-11-15 实机依据：njyuantai01 这一家列表卡带的 id 回的是 25258 字节通用壳（连「公司名片」
# 块都没有），而这一页 200 且列着本店 20 个在架产品，取第一个 id 去产品页即得全量名片
# （手机 13675360858 / 张艳美 / 0536-2105088）——所以「id 不行」不等于「店不行」。
SHOP_PRODUCT_LIST_URL = "http://{slug}.cn.makepolo.com/companyProduct.html"
# 产品详情页（站方主域）：实测这一页把**手机号明文**写在「公司名片」里，而商铺页 contact_us
# 把同一个号藏成图片码。列表卡就带 proid，所以这一页是一跳可达的免费明文源。
#
# 【为什么是 http 而不是 https】2026-11-15 实机取证：
#   ① 搜索结果页里站方自己给的产品链接就写成 `http://china.makepolo.com/product-detail/….html`；
#   ② 这一台主机在 https:443 上吐的是**火山引擎 WAF 的默认证书** `CN=*.volcwaf001.com`
#      （SAN 只有 *.volcwaf001.com / volcwaf001.com），对 china.makepolo.com 是 hostname mismatch，
#      每一次 https 请求都在 TLS 阶段就抛 SSLError——不是站点在拦，是我们自己把主通路
#      （明文手机唯一来源）整条掐了，且旧写法把这一下记成「导航失败」，界面上只看到「详情失败」。
#   证书用不校验的方式握手是通的（TLSv1.3 协商正常），说明是**站点侧证书装错**而不是网络不通；
#   校验本来就不该关（关了就是把中间人风险换成便利），而站方自己指的 http 这条路既通又给全量数据。
PRODUCT_URL = "http://china.makepolo.com/product-detail/{pid}.html"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 产品卡：区域 + 公司名 + 商铺子域 + 主营（三链接并列：首页 / companyProduct / contact_us）
_CARD_RE = re.compile(
    r'class="s_product_contact[^"]*">(.*?)</div>', re.S)
# 【列表项整块】2026-11-16 实机取证（马可波罗 51 家里 50 家「手机/固话/邮箱/微信」四项全空）：
# 站方已经把产品 id 从「contact_us.html?proid=…」挪到了**产品标题链接**
# `//china.makepolo.com/product-detail/<id>.html`，它落在 `s_product_item` 块的上半区，
# 而本文件过去只遍历 `s_product_contact` 那一小段——于是 proid 恒空，「产品页明文手机」这条
# docstring 里写着「实测最硬的一条通路」的腿整轮**一次请求都没发过**，且账上没有一行说过
# 这件事（`src_count` 只累加、从没被打出来过）。所以这里按整块取：产品 id 与联系块同属一家。
# `_CARD_RE` 仍留着：公司名/主营/属地照旧从联系块里取，口径与旧版一字不动（少改一处是一处）。
_ITEM_RE = re.compile(r'class="s_product_item[^"]*">(.*?)(?=<div class="s_product_item|\Z)',
                      re.S)
_CITY_RE = re.compile(r"\[([\u4e00-\u9fa5A-Za-z ]{2,20})\]")
_SHOP_RE = re.compile(r"http://([\w\-]+)\.cn\.makepolo\.com/")
_NAME_RE = re.compile(r'cn\.makepolo\.com/"[^>]*>\s*([^<]{4,60}?)\s*</a>')
_PROD_RE = re.compile(r'companyProduct\.html"[^>]*>\s*([^<]{4,300}?)\s*</a>')
# 列表卡里就带产品 id（contact_us.html?proid=…）：拿着它就能一跳跳到写了明文手机的产品页
_PROID_RE = re.compile(r'(?:contact_us\.html\?proid=|product-detail/)(\d{9,14})')
# 号码图片码：通道名(t_page) 与密文 token 都在 URL 里，token 长度直接给出号码字符数。
# `buildStringToimg2?\.php` 里的 `2?` 是 2026-11-15 实机补的：商铺首页 block2 壳挂的是
# buildStringToimg**2**.php（多两个参数 fei=1|2 与 en_corpid=<每店一份的密文>），旧写法
# `buildStringToimg\.php` 正好被那个 2 顶开，整页码图一张都不认（通路账上的「码图 0 次」就是这么来的）。
_CODE_RE = re.compile(r'buildStringToimg2?\.php\?t_page=(\w+)&phone=([^"\'\s&]+)')
# 码图的**整条 src**：请求时必须照页面上写的来（含 fei/en_corpid），少一个参数站方就给 0 字节。
_CODE_SRC_RE = re.compile(r'(?:src|data-src|data-original)\s*=\s*["\']'
                          r'([^"\'\s]*buildStringToimg2?\.php\?[^"\'\s]*)["\']', re.I)
# 产品中心页里的自家产品 id：两种形状都认（列表卡给 china.makepolo.com/product-detail/<id>.html，
# 商铺自家给 {slug}.cn.makepolo.com/product/<id>.html）。
_CP_PID_RE = re.compile(r'/product(?:-detail)?/(\d{9,14})\.html')
# 【码图归属本店判据①】挂在「手机/电话/固定电话/联系电话」标签作用域里的码图＝本店登记联系方式。
# 用字段语义而不是区块类名划线，是因为实测里同一份号码会同时出现在好几个容器
# （connect_msg 的「手机：」、弹窗「供应商：」、cont_num_box2 的「联系电话」框、首页 block2 的
# 「yellow_left_mobile_cpc」），而推广块 cont_num_box2 装的偏偏也可能是本店号——按类名黑白名单必错。
# `_SPAN_OPEN` 允许标签带属性、并允许值落在紧接其后的那一个 span/li 里（首页 block2 就是这么写的）；
# 只多跨**一层开标签**，所以空值栏仍停在它自己的闭标签上，不会把下一栏的值串过来。
_SPAN_OPEN = r"(?:<span[^>]*>|<li[^>]*>)"
_SPAN_NEXT = r"(?:</span>\s*(?:<br\s*/?>\s*)?" + _SPAN_OPEN + r"|\s*)"
_CONTACT_SCOPE_RE = re.compile(
    _SPAN_OPEN + r"\s*(?:手机|电话|固定电话|联系电话)\s*[：:]\s*" + _SPAN_NEXT +
    r"(.*?)\s*(?:</span>|</li>)", re.S)
# 明文字段：A 型值在同一条 span、B 型在 <li>、首页 block2 型值在紧接其后的 span，三种一并抓
# （标签词按三种模板的写法列全）
_FIELD_RE = re.compile(_SPAN_OPEN + r"\s*(联系人|联系电话|电话|手机|固定电话|传真|Email|邮箱|"
                       r"公司地址|地址|邮政编码|QQ)\s*[：:]\s*" + _SPAN_NEXT +
                       r"(.*?)\s*(?:</span>|</li>)", re.S)
_META_RE = re.compile(r'<meta\s+content="([^"]{20,400}?)"\s+name="description"', re.S)

# ── 产品详情页的「公司名片」：手机号明文就写在这里，而且同一张卡里自带归属自证 ──
#   <h2>公司名片</h2><ul class="mes_cont">
#     <li>手机号：<span>13450460020 </span></li>
#     <li>联系人：<span>黄亨</span></li>
#     <li>公司名称：<span><a href="//dgghzl.cn.makepolo.com">东莞市港汇制冷机…</a></span></li></ul>
# 必须按块取：全文扫手机号会挖到 appid=1620789529555202、统一社会信用代码
# 91441900551720725X、页内十六进制跟踪串里的假号（实测都能凑出 11 位“合法手机”）。
_PD_CARD_RE = re.compile(r"<h2>公司名片</h2>\s*<ul[^>]*>(.*?)</ul>", re.S)
_PD_MOBILE_RE = re.compile(r"手机号\s*[：:]\s*<span>\s*(1[3-9]\d{9})\s*</span>")
_PD_PERSON_RE = re.compile(r"联系人\s*[：:]\s*<span>\s*([^<]{2,20}?)\s*</span>")
_PD_OWN_RE = re.compile(r'href="//([\w\-]+)\.cn\.makepolo\.com"')
# 供应商信息块：站方自己防爬，字段名拆成 <span>电</span>话 这种写法，只能按原样认
_PD_SUP_RE = re.compile(r'<div class="supplier_b_mes">(.*?)</div>', re.S)
_PD_TEL_RE = re.compile(r"<span>电</span>话\s*[:：]\s*(?:&nbsp;|\s)*([\d\-+() ]{7,20})")
_PD_FAX_RE = re.compile(r"<span>传</span>真\s*[:：]\s*(?:&nbsp;|\s)*([\d\-+() ]{7,20})")
_PD_ADDR_RE = re.compile(r"<span>地</span>址\s*[:：]\s*(?:&nbsp;|\s)*([^<]{6,90})")
_PD_NAME_RE = re.compile(r'<p class="n">([^<]{2,20})</p>\s*<p class="p">\s*(1[3-9]\d{9})?\s*</p>')
_TAG_RE = re.compile(r"<[^>]+>")
# HTML 注释里的东西页面上不展示，一律不算联系方式（与 _in_comment 对码图的处置同一条理由）：
# 实测商铺首页在码图旁边并排写着 `<!-- <em class="show_sub_cpc_fixphone">025-****1400</em>-->`
# 这类「站方自己掩好位等填」的残留，跨栏取值一放宽就会被读进字段文本里。
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 字段值里挂着的码图整段摘掉（连 `&fei=…&en_corpid=…` 的尾巴一起）：`_CODE_RE` 的字符类
# 在 & 处就停了，只摘掉前半段会把参数留在文本里，看着就像一栏有值。
_CODE_STRIP_RE = re.compile(r"buildStringToimg2?\.php\?[^\s\"'<>]*")
# 站方模板假值（实测库里出现，入库就是空号/客服号）
_FAKE_NUMBERS = {"17368614555", "400-000-0000", "13800138000"}
_FAKE_MAILS = ("makepolo.com", "makepolo.cn")
# 运营商手机邮箱：这些域是按号段分配给机主的，前缀 11 位手机号即本机号
_CARRIER_MAIL = ("139.com", "189.cn", "wo.cn")

# ── 数据质量清洗·邮箱与联系人（实机跑 43 家里各抓到 1 家；另两处见模块 docstring：实体残留、假区号固话）──
# ① 占位/写坏的邮箱：实测「test@2449181705qq.com」——本名 test 是站方演示模板留下的，
#    域名首段又是 10 位数字打头（商家把 QQ 号抄进了域名、@ 前后写反）。这种地址发过去
#    必退信。按「宁缺不假」直接作废，绝不猜成 2449181705@qq.com（那是推断出来的地址，
#    猜错就把邮件发到陌生人邮箱）。实测两页真页里都没有 test@ 形态，确属商家自填。
_BAD_MAIL_LOCAL = {"test", "demo", "guest", "sample", "aaa", "abc", "xxx", "asdf",
                   "qwe", "123", "admin", "email", "mail"}
_BAD_MAIL_DOMAIN_RE = re.compile(r"^\d{6,}[a-z]*\.(com|cn|net)$")
# ② 商家把「产品词 + 姓 + 称谓」连写进联系人栏（实测「热处理备件杨经理」「制冷设备李经理」）。
#    基类的 parse_contact_field 只认尾巴上的称谓，前缀产品词会跟着名字一起入库，外呼开口
#    就成了「热处理备件杨经理」。这里按姓氏表把前缀切掉，且只在切出来的尾巴确实是
#    〔姓 + 称谓〕时才切；切不出来就原样交回基类，绝不拿产品词去猜名字。
_MP_COMPOUND_SURNAME = ("欧阳", "司马", "上官", "诸葛", "东方", "独孤", "慕容", "皇甫",
                        "尉迟", "公孙", "长孙", "宇文", "轩辕", "南宫", "夏侯", "百里")
# 常见单姓（按人口覆盖取前 ~110，未覆盖的稀有姓走“原样保留”分支，不会误伤）
_MP_SURNAME_CHARS = (
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡"
    "余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯"
    "龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
_MP_TITLE_TAIL = ("经理", "总监", "厂长", "科长", "主任", "老板", "先生", "女士", "小姐",
                  "总", "生", "哥", "姐")
_SURNAME_ALT = "%s|[%s]" % ("|".join(_MP_COMPOUND_SURNAME), _MP_SURNAME_CHARS)
_MP_PERSON_PREFIX_RE = re.compile(r"^(.{2,}?)(%s)(%s)$" % (_SURNAME_ALT, "|".join(_MP_TITLE_TAIL)))
# 整串就是「姓 + 称谓」的形状（用于判定并列多人：周先生/李女士 两边都是这形状才取第一位）
_MP_PERSON_TAIL_RE = re.compile(r"^(?:%s)(?:%s)$" % (_SURNAME_ALT, "|".join(_MP_TITLE_TAIL)))
# ③ 产品词也粘在**姓名后面**（实机文安县汇永商贸：名片写「田经理二手机床」——商家把
#    「二手机床」这个类目词连着联系人一起填，基类按尾巴上的称谓取名就切成「田经理二」）。
#    切后缀的代价比前缀高：「李总经理」「王经理助理」这种「姓+称谓+称谓/职务」是**合法写法**，
#    切了就把职称切碎。所以只切「后面接的词不是职务/部门词」的那些（多半就是产品类目词）。
_MP_ROLE_CONT = ("经理", "总监", "主管", "主任", "专员", "助理", "厂长", "工程师", "技术员",
                 "先生", "女士", "小姐", "老师", "科长", "总经理", "业务", "销售", "外贸",
                 "内销", "内贸", "客服", "市场", "技术", "部门", "副总", "老板")
_MP_PERSON_SUFFIX_RE = re.compile(r"^(%s)(%s)(.{2,})$" % (_SURNAME_ALT, "|".join(_MP_TITLE_TAIL)))


def _strip_product_prefix(text):
    m = _MP_PERSON_PREFIX_RE.match(text)
    return (m.group(2) + m.group(3)) if m else text


def _strip_product_suffix(text):
    m = _MP_PERSON_SUFFIX_RE.match(text)
    if not m or m.group(3).startswith(_MP_ROLE_CONT):
        return text
    return m.group(1) + m.group(2)


def _strip_product_affixes(text):
    """先试切前缀、再试切后缀（切完前缀的串必定以「姓+称谓」收尾，后缀那一条自然不命中，
    两步可以安全串联）。实机只出现过**单侧粘连**：「热处理备件杨经理」是前粘、
    「田经理二手机床」是后粘；两侧同时粘（「数控车床张经理二手机床」）没在真页里见过，
    也就不去中间挖名字——只认贴着串首或串尾的形状，中间挖容易把「不锈钢经理」
    「剩余生意」这类产品词里的字当成人名，宁可不切也绝不猜名字。"""
    return _strip_product_suffix(_strip_product_prefix(text))


def clean_contact_person(raw):
    """联系人原串 → 去掉粘在姓名**前后**的产品/类目词，并列多人取第一位。
    纯函数，切不动就原样返回；且是**定点**——对已清洗的值再跑一次不会变更（离线断言钉住）。"""
    text = re.sub(r"\s+", "", str(raw or ""))
    if not text:
        return str(raw or "")
    parts = [p for p in re.split(r"[/\u3001，,&＆]", text) if p]
    named = [p for p in parts if re.search(r"\D", p)]      # 丢掉纯数字那段（姓名栏粘着号码）
    if len(parts) >= 2 and named and all(_MP_PERSON_TAIL_RE.match(_strip_product_affixes(p))
                                         for p in named):
        text = named[0]        # 实机见过「周先生/李女士」「张经理/138…」：基类会并成没法叫的一串
    text = re.sub(r"[/\u3001，,&＆\s]+$", "", text)      # 并列/接号留下的顿号尾巴
    return _strip_product_affixes(text)


# 站方实体写法很随手（&nbsp 不带分号、甚至双重转义成 &amp;nbsp）。实测口径：
# html.unescape 认得**不带分号的具名实体**（「张新鲜&nbsp」→ \xa0，随空白一起收掉），
# 真正吃不掉的是双重转义后剩下的那截「&nbsp」。所以顺序是先 unescape 再补一道残体清洗，
# 而不是先洗再 unescape——先洗会把 &#39; 这类数字实体从中间劈开、留下孤零零的分号。
_ENTITY_RESIDUE_RE = re.compile(
    r"&(?:nbsp|ensp|emsp|thinsp|quot|apos|amp|lt|gt|#\d{1,6})(?![a-zA-Z0-9])", re.I)


def clean_text(v):
    """去标签 → unescape 正规实体 → 再洗 unescape 吃不掉的残体 → 收拢空白，两头清冒号。"""
    v = _htmlmod.unescape(_TAG_RE.sub(" ", str(v or "")))
    return re.sub(r"\s+", " ", _ENTITY_RESIDUE_RE.sub(" ", v)).strip(" ：:")


def is_bad_email(email):
    """邮箱该不该入库：占位本名（test/demo…）或域名首段是一串数字（QQ 号被抄进域名）都算坏。"""
    mail = str(email or "").strip().lower()
    if "@" not in mail:
        return True
    local, _, domain = mail.partition("@")
    return local in _BAD_MAIL_LOCAL or bool(_BAD_MAIL_DOMAIN_RE.match(domain))


class MakepoloCrawler(CdpSiteCrawler):
    PLATFORM_NAME = "马可波罗"
    PLATFORM_SLUG = "makepolo"
    BASE_URL = HOME_URL
    HOME_DOMAIN = "makepolo.com"
    NEEDS_BROWSER = False           # 全程裸 HTTP（号码图也一样靠会话 Cookie 取，不需要 JS）
    BLOCK_OPEN_BROWSER = False      # 本站只有会话/限流型拦截，没有可人工过的滑块
    HTTP_BLOCK_MARKS = CdpSiteCrawler.HTTP_BLOCK_MARKS + (
        "请输入验证码", "访问过于频繁", "系统繁忙", "安全验证")
    HTTP_BLOCK_MAX_BYTES = 2000     # 薄壳（无卡片）与正文 181KB 差两个量级
    MIN_PAGE_CHARS = 1500
    PER_REQUEST_PACE = True         # 一店＝1 页 + 若干码图，节流必须下沉到请求级
    PACE_FACTOR = 0.8
    MIN_DELAY = 1.6
    # 每关键词页数硬上限：界面下发「不限页数」时会覆盖掉 __init__ 里的默认值，
    # 而本站一个关键词就有上百万条产品（实测「制冷设备」1_429_811），不限页会失控。
    # 取 5 页的依据：每页约 22 家、号码图按店放图（12.6%）→ 5 页≈110 家≈ 14 家能出图，
    # 再往后翻与前面类目高度重复，边际收益递减而限流风险递增。
    MAX_PAGES_HARD_CAP = 5
    # 【图片码策略的数据根据】406 家店 × 页面内号码图 URL 共 1_919 个 token，实际只拿到 189 张 PNG：
    #   出图是**按店**而不是按张随机——406 家里 51 家（12.6%）放图，且这 51 家里有 25 家一放就是 5、6 张。
    #   所以用「探测两张→命中就把这一家其余全取→未命中再补一张」的两阶段取法（历史数据回测）：
    #     探测1+补1 → 召回 94.7%、2.21 请求/店；探测2+补1 → 召回 98.4%、2.93 请求/店；全打 → 100%、4.73 请求/店。
    #   取中间那一档：几乎不丢号码，请求量比“全打”省 38%。
    IMG_PROBE_CODES = 2         # 探测前两张（两家不中才补，单张探测会漏「只有靠后那张出图」的店）
    IMG_PROBE_FALLBACK = 1      # 探测全空时再补一张（这就是那 3.7% 的差别所在）
    # 站点级熔断：连续这么多**家**一张 PNG 都没拿到，才认定图片服务这段时间关了（12.6% 出图率下
    # 30 家全不中≈ 1.6% 的事件，正常跑不会碰到；碰到了就再不停手只会白耗配额）。
    # 旧版这里是 8，实测把一整轮的图片通路过早关掉、手机号直接归零，属于误伤，已改为保守阈值。
    IMG_DEAD_STREAK = 30
    # 跨店公共号阈值：同一个号挂在超过这么多**家**不相关公司页上，就不是本店电话。
    # 归属过滤（_shop_code_tokens）是第一道闸，这一条是防站方改版换壳的第二道闸。
    PUBLIC_NUM_MAX_SHOPS = 2
    # 同店其它产品页最多试几个（_pd_alternates）：实测 48% 的店在列表里就有 2 个以上
    # 产品 id，试两个还拿不到基本就是整店没登记手机，再打只会白耗流量。
    PD_ALT_TRIES = 2
    # 第三条腿（_shop_product_alternates）的成本闸：一店最多取几个自家产品 id、
    # 一轮最多为几家走这条路。取 2 个 id 与 PD_ALT_TRIES 同档（实测第一个就出货的占多数）；
    # 上限 20 家的依据是「一条不通的腿不该吃掉整轮预算」——一店这一跳是 1 + 2 个请求，
    # 20 家 ≈ 60 个请求，与一店三张码图的探测量同级，超出后只在日志里说一次。
    CP_PID_TRIES = 2
    CP_SHOP_CAP = 20

    def __init__(self, port=9255):
        super().__init__(port)
        self.delay_between = 2.5
        self.jitter = 1.5
        self.max_pages = self.MAX_PAGES_HARD_CAP   # 一词 5 页 ×≈22 家（上面注释里有取数理由）
        self._session = self._new_session()
        self._ocr = load_default_font()
        self._img_dead = 0
        self._img_off = False
        self._num_shops = {}          # 号码 → 见过它的店铺集合（跨店公共号哨兵用）
        self._last_status = 0         # 最近一次 _fetch 的 HTTP 状态码（用来区分 439 死铺与导航失败）
        # 号码通路账目（结束日志用，让用户看见「手机号是从哪几条路拿到的」）。
        # 键 = **号码来源**而不是抓取路径：三条产品页路径（列表卡 id / 同店其它 id / 自家产品中心
        # id）拿到的都是同一种「产品页公司名片」明文号，混进一个键才不加戏；第三条腿自己走了几家、
        # 出货几家另记在 _cp_shops/_cp_hits（渲染见 stats()["cp_leg"]），免得同一个号被两条腿各记一次。
        self.src_count = {"产品页": 0, "码图": 0, "子域": 0, "邮箱前缀": 0, "明文": 0}
        self._cp_shops = 0            # 第三条腿为几家发过产品中心页这一跳
        self._cp_hits = 0             # 其中靠它拿到明文手机的家数
        # 通路「有没有原料」的账（口径见 _leg_warn / stats）：认出的卡数、其中带产品 id 的张数、
        # 已经开口说过的取证句（同一句一轮只说一次）
        self._leg_cards = 0
        self._leg_pid = 0
        self._leg_said = set()

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_URL,
        })
        return s

    def reset_run_state(self):
        super().reset_run_state()
        self._img_dead = 0
        self._img_off = False
        self.src_count = {"产品页": 0, "码图": 0, "子域": 0, "邮箱前缀": 0, "明文": 0}
        self._cp_shops = 0
        self._cp_hits = 0
        self._leg_cards = 0
        self._leg_pid = 0
        self._leg_said = set()
        return True

    def _leg_warn(self, why):
        """「某条通路这一次一根原料都没拿到」那一类话只说一次（每页都会调 parse_list，
        逐页播报会把这一句刷成屏，而刷屏的结局是没人读——与 `_note_deep_block` 同一族纪律）。"""
        if why not in self._leg_said:
            self._leg_said.add(why)
            self._log(f"  ⚠ 通路取证：{why}")

    def stats(self):
        """基类账目 + 本站独有的「号码是从哪条腿来的」与「这条腿有没有原料」。
        上一轮就是缺这一格：产品页那条腿整轮 0 次、`src_count` 只累加从不外读，
        于是「51 家 50 家四项全空」在账上长得跟「平台就是不给号码」一模一样。"""
        st = super().stats()
        st["legs"] = dict(self.src_count)
        st["leg_pid_raw"] = f"{self._leg_pid}/{self._leg_cards}"
        # 第三条腿的账（走了几家 / 靠它出货几家）：这一条 0 次调用意味着「列表卡给的 id
        # 全不给看」的店本轮只能靠码图，那是诊断信息而不是号码来源，所以不并进 legs。
        # 一家都没走过时不给键——渲染层拿不到就不显示，避免在别的站上凭空多一段空账。
        if self._cp_shops:
            st["cp_leg"] = f"自家产品中心 {self._cp_hits}/{self._cp_shops} 家"
        return st

    # ── 旁路浏览器：本站不占用标签 ────────────────────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        """本站不常驻标签，但借来的一枚必须还（空壳会把为跑完 JS 质询壳而借的标签留到
        收工之后；由来与判据见 cdp_base.CdpSiteCrawler.release_tab）。"""
        self.release_tab("收工")
        return None

    def _is_session_alive(self):
        return True

    def probe_block(self):
        """恢复探测：真取一页搜索并解析得出公司卡才算解除（限流薄壳也是 200）。"""
        url = self._block_probe_url or self.build_search_url("公司", 1)
        # 熔断＝这次探测没发出去，说成「仍在限流」就是把网络账记到站点头上
        # （前缀口径见 cdp_base._skip_dead_host_probe）
        fuse = self._skip_dead_host_probe(url, "恢复探测")
        if fuse:
            return fuse
        try:
            r = self._session.get(url, timeout=http_timeout(25), headers={"Referer": HOME_URL})
            # 站点有回应＝这台主机还活着，顺手给熔断账销一次（口径见 cdp_base._mark_activity）
            self._mark_activity()
            if r.status_code == 200 and not self.http_block_reason(r.status_code, r.text):
                if self.parse_list(self._decode(r, "gb18030"), "公司"):
                    self._block_reason = ""
                    return ""
        except Exception as e:
            # 旧写法 `except: pass` 会把「连不上」说成「马可波罗仍在限流状态」：那条处方是
            # 等冷却／换 IP，而这里要的是换出口网络（口径见 cdp_base._probe_net_fail）
            return self._probe_net_fail(e)
        return self._block_reason or "马可波罗仍在限流状态"

    @staticmethod
    def _decode(r, enc):
        """编码分域：搜索域 gbk/gb18030，商铺子域 utf-8（实测两者不同，解错就整页乱码）。"""
        try:
            return r.content.decode(enc, "ignore")
        except Exception:
            return r.text or ""

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer="", deep=False):
        """取一页。`deep=True`＝请求的是**某一家的深链**（产品页/联系页/商铺首页），
        不是通道入口：它的被拒只记深链单笔账，不写 `_block_reason`、不钉恢复探测页、
        不触发整通道冷却。理由与实测数据见 cdp_base.http_block_reason 的 deep 分支。"""
        # 主机级熔断（口径与由来见 cdp_base._skip_dead_host）：这一台已经连续两次连不上，
        # 本轮不再为它付整轮连接超时——跳过之后由翻页层照「一页都没取到」归因，不谎报「无数据」
        if self._skip_dead_host(url):
            return ""
        last = ""
        deep_refused = ""         # 非空＝这一跳是被拒的深链（原因），见循环内 deep 分支
        self._last_status = 0
        for i in range(max(1, tries)):
            try:
                self._pace_before_request()
                r = self._session.get(url, timeout=http_timeout(25),
                                      headers={"Referer": referer} if referer else None)
                self._mark_activity()
                self._last_status = r.status_code
                text = self._decode(r, enc)
                block = self.http_block_reason(r.status_code, text, url, r.headers, deep=deep)
                if block:
                    if deep:
                        self._note_deep_block(url, block)
                        deep_refused = block
                        # 200·JS 质询壳是这里唯一「机器自己就能解」的一档：借一枚标签把那段
                        # JS 跑完、抄回通行证，再重试这一跳（403/439 那种资源级被拒开标签也
                        # 没用，白烧一个标签）。判据、预算、成败口径都在基类那把手里（理由
                        # 见 cdp_base._deep_shell_retry），本站只负责「再取一次」怎么取。
                        got = self._deep_shell_retry(
                            url, r.status_code, text,
                            lambda: self._fetch(url, enc=enc, tries=1, guarded=False,
                                                referer=referer, deep=deep))
                        if got is not None:
                            return got
                        break
                    self._block_reason = block
                    last = f"HTTP {r.status_code}（防采插页）"
                    break
                if r.status_code == 200 and text:
                    self._block_reason = ""
                    return text
                last = f"HTTP {r.status_code}"
                if r.status_code in (404, 439):
                    # 439 是站方给「这个产品页不给看」的固定回应（实测：同一时刻同域其它
                    # 产品页仍 200，等 45s 重试还是 439）——是资源级不可用而不是限流，
                    # 所以不设拦截原因、不白重试，由详情逻辑落到 contact_us 兜底。
                    return ""
            except requests.exceptions.ConnectionError as e:
                last = self._shape_request_error(e)
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = self._shape_request_error(e)
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(url, enc=enc, tries=tries, guarded=False,
                                   referer=referer, deep=deep)
        # 深链的**状态码级**被拒：只记深链那一本账，不进通道入口的「取页失败」账，也不在
        # 这里刷日志（`_note_deep_block` 首次已在主机级打过一行，逐家再打会把整轮日志刷满
        # 同一句话）。2026-11-15 实机马可波罗 contact_us 大面积 403：旧写法既写
        # `_block_reason` 又把探测页钉在那条永远 403 的深链上，恢复探测打不通、整条通道
        # 白等 765 秒。分账之后深链自己不再触发等待；上面那段等待只在**通道级**确实被
        # 标记拦停时发生——那种等待探的是入口页、等得到结果，能让后面整批深链一起复活，
        # 所以这一段短路必须留在等待**之后**（跳过等待才是真的丢数据）。
        if deep_refused:
            return ""
        # 深链被拒**之外**的失败照旧记：连不上是主机的属性，与请求哪一页无关，
        # 该累计连击、该拉黑就拉黑（口径见 cdp_base._note_fetch_fail）。
        self._note_fetch_fail(last, deep)
        self._log(f"  ⚠ 马可波罗请求失败（{last}）：{url[:80]}")
        return ""

    # ── 列表 ───────────────────────────────────────────────────
    def build_search_url(self, kw, page):
        url = SEARCH_URL.format(kw=quote(str(kw).encode("gb18030", "ignore")), page=max(1, int(page)))
        # 省份精准采集：页面地区列表由 search_alljs 的 search_page() 拼 `&ae=<元素id>`，
        # id 就是省份拼音（注意山西 shanxi／陕西 shanxii）。2026-11-04 探针 A/B 实锤
        # 服务端真过滤（ae=guangdong 与全国结果集重合仅 2/26）。
        # 多省场景：URL 参数只能指向一个省，多省时放弃参数直筛，退回数据层过滤。
        pv = getattr(self, "province", "")
        if pv and "," not in pv:
            ae = _pf.param_value("makepolo", pv, "pinyin")
            if ae:
                return f"{url}&ae={ae}"
        return url

    def parse_list(self, html, kw):
        """产品卡 → 公司条目（按商铺子域去重；同店多产品只留首卡，但把其它产品 id
        收进「备选产品ID」——实测三页 50 家里 48% 有 2 个以上产品 id，首卡那个产品页
        不给看时还有得换，而且不额外花列表请求）。

        同店多卡还合并**主营全文**（只喂相关性判定，不进记录）：实机「数控机床」两轮里
        出现过同一家两张卡类目词完全不同字的情况（深圳市鲁海阳精密机械配件：一张写
        「刀片,刀夹,其他机床附件,刀杆,夹头」、另一张写「其他通用五金配件,钻头,螺母,铣刀」），
        而翻页时首卡先被标记已见、后面的卡直接跳过——只看首卡判相关就会把真同行误杀。"""
        out, idx = [], {}
        # 整块优先；站方模板又改了、整块认不出来时退回旧的「联系块」遍历——退回必须当场
        # 说出来，因为旧形状里取不到产品 id，那条腿等于没原料（见 _ITEM_RE 的实机取证）。
        chunks = _ITEM_RE.findall(html or "")
        if not chunks:
            chunks = [c for c in _CARD_RE.findall(html or "")]
            if chunks:
                self._leg_warn("列表项整块认不出来，已退回旧的「联系块」取法"
                               "（这种形状下产品 id 取不到，明文手机那条腿没原料）")
        for chunk in chunks:
            seg_m = _CARD_RE.search(chunk)
            seg = seg_m.group(1) if seg_m else chunk
            sm = _SHOP_RE.search(seg) or _SHOP_RE.search(chunk)
            if not sm:
                continue
            slug = sm.group(1).lower()
            pids = _PROID_RE.findall(chunk)
            self._leg_cards += 1
            if pids:
                self._leg_pid += 1
            pm = _PROD_RE.search(seg)
            prod = clean_text(pm.group(1)) if pm else ""
            if slug in idx:
                e = out[idx[slug]]
                # 同店多卡：除首卡那个号以外的产品 id 都收进备选，供 `_pd_alternates` 换页重试
                for alt in pids:
                    if alt and alt != e["产品ID"] and alt not in e["备选产品ID"]:
                        e["备选产品ID"].append(alt)
                if prod and prod not in e["主营全文"]:
                    e["主营全文"] = (e["主营全文"] + " " + prod)[:600]
                continue
            nm = _NAME_RE.search(seg)
            name = clean_text(nm.group(1)) if nm else ""
            if len(name) < 4:
                continue
            if any(x in name for x in ("首页", "登录", "注册", "更多", "下一页")):
                continue
            cm = _CITY_RE.search(seg)
            idx[slug] = len(out)
            out.append({
                "公司名称": name[:60],
                "公司ID": slug,
                "memberId": slug,
                "产品ID": pids[0] if pids else "",
                "备选产品ID": [],
                "公司网址": f"http://{slug}.cn.makepolo.com/",
                "详情URL": SHOP_URL.format(slug=slug),
                "主营产品": prod[:200],
                # 本店这一页所有卡的主营并起来，只给早过滤判相关用（记录里仍是首卡的主营产品）
                "主营全文": prod[:600],
                "经营模式": "",
                "所在区域": re.sub(r"\s+", " ", cm.group(1)).strip()[:30] if cm else "",
                "标题": name[:80],
            })
        # 整轮都没拿到产品 id＝产品页那条腿一根原料都没有（与「腿通了但这家不给看」是两件事：
        # 后者在 deep_block 账里，前者只有这一格说得出）。不响这一句的话，「50 家四项全空」
        # 就只能被读成「平台不给号码」，正是上一轮缺的那句。
        if self._leg_cards and not self._leg_pid:
            self._leg_warn(f"列表 {self._leg_cards} 张卡一张都没带产品 id，"
                           "「产品页明文手机」这条腿本轮没原料（多半是站方又改了列表模板），"
                           "号码只剩联系页/商铺首页的码图一条路")
        return out

    def stub_relevance_text(self, stub):
        """本站早过滤把**公司名**也算进相关文本（基类默认只看主营产品）。
        理由是实测数据：马可波罗的产品卡「主营产品」写的是类目词，与关键词常常不同字
        ——「制冷设备」三页 69 张卡里，只看主营产品只放过 43%，而公司名里就带着
        「制冷设备/冷气/冷却」的同行被误杀 17 家（占 25%），例如
        「天津良丰制冷设备有限公司 / 冷却塔」「昆山格律斯机械设备制造有限公司 / 冷冻机,
        冷水机、制冰机」。加上公司名后放过 68%，砍掉的确实是「减速器/五金工具/清洗剂」
        这类不相关类目。相关性判定只会因多一个字段而更宽（is_relevant 是命中即放行），
        所以这一改只找回数据、不会新增误杀。同店多张卡还并进「主营全文」一起判（见 parse_list）。"""
        return " ".join(str(stub.get(k) or "")
                        for k in ("公司名称", "主营产品", "主营全文", "主营商品", "标题"))

    def slice_total(self, html):
        m = re.search(r"共找到\s*<span[^>]*>\s*([\d,]+)\s*</span>", html or "")
        return int(m.group(1).replace(",", "")) if m else -1

    def list_has_more(self, html, page):
        return bool(html) and ("下一页" in html or "pg=%d" % (page + 1) in html)

    # ── 列表翻页：必须自己走裸 HTTP（基类的 _iter_keyword 是 _goto + _html 标签路径）──
    def _iter_keyword(self, kw):
        """与 maimaib2b/aipage 同口径：取页必须过本类 _fetch（才有防采分诊/限速/编码分域），
        记账字段（stat_*、_seen_members、_early_skipped）与基类逐字对齐。"""
        empty = stale = 0
        page = 0
        while not self._stop_requested:
            page += 1
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = self.build_search_url(kw, page)
            html = self._fetch(url, enc="gb18030", referer=HOME_URL)
            if not html:
                self._note_page_fail()
                self._log(self._no_result_line(kw, f"第 {page} 页", html) + "，停止翻页")
                return
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            # 条目账走唯一把手（总账 + 分词账 + 站方自报账）：见 cdp_base._kw_zero_reason
            self._note_listed(kw, len(entries), self.parse_listed_total(html))
            if not entries:
                empty += 1
                if empty >= 2:
                    self._log(self._no_result_line(kw, f"第 {page - 1} 页起", html)
                              + "，停止翻页")
                    return
                continue
            empty = 0
            new = skipped = seen = 0
            for e in entries:
                mid = e["公司ID"]
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e["公司名称"])
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    seen += 1
                    self._incremental_skipped += 1
                    continue
                # 早过滤：产品卡上的公司名/主营与关键词无关的，连联系方式页都不进（省请求＝提速＋防封）
                # 砍掉的**只按「关键词#主体」标记**，不占全局已见：相关性是「对这个关键词」的判定，同一家对 A 词不符、对 B 词可能正是目标（实机砍掉的名单里就有真同行）。
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._note_early_skip(kw, e["公司名称"])
                    self._seen_members.add(f"{self.PLATFORM_SLUG}:{kw}#{mid}")
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e["公司名称"], "memberId": mid})
                    continue
                self._seen_members.add(key)
                self._seen_members.add(nkey)
                e["关键词"] = kw
                new += 1
                yield e
            tail = self._page_tail(skipped, seen)
            self._log(f"  「{kw}」第 {page} 页：{len(entries)} 家，新增 {new} 家{tail}")
            if new == 0:
                stale += 1
                if stale >= 2:
                    self._log(f"  「{kw}」连续 {stale} 页无新增，公司池已枯竭")
                    return
            else:
                stale = 0
            if not self.list_has_more(html, page):
                return
            self._sleep()

    def _public_num_hit(self, num, slug):
        """跨店公共号哨兵：同一号码挂在越来越多的不相关公司页上，就不是「这家店的电话」，
        而是站方推广位/中介号/集团总机。实测 CPC 弹窗那两个上海号就是同时挂在
        长沙泵厂与厦门微电子两家店上的。归属过滤靠 DOM 语义，站方一改版就可能失效；
        本哨兵靠数据本身的重复度兜底，两道闸互补。
        前两家保留（一个集团开两三家店是常态），第三家起不再入库。"""
        shops = self._num_shops.setdefault(num, set())
        shops.add(str(slug or "").lower())
        if len(shops) == self.PUBLIC_NUM_MAX_SHOPS + 1:
            self._log(f"  ⚠ {num} 已出现在 {len(shops)} 家不同公司页上，判为公共号"
                      f"（站方推广位/中介号），从本店记录里剔除")
        return len(shops) > self.PUBLIC_NUM_MAX_SHOPS

    # ── 码图归属：只收「确实属于本店」的那几张 ──────────────────
    @staticmethod
    def _in_comment(page, idx):
        """该位置是否落在 HTML 注释里（站方旧模板残留，页面上不展示，不算联系方式）。"""
        return page.rfind("<!--", 0, idx) > page.rfind("-->", 0, idx)

    def _shop_code_tokens(self, page, slug):
        """返回本页属于本店的码图 [(style, token, 页面原样 src)]，按页面出现顺序去重。

        第三个元素是**页面上写着的那条 URL**（2026-11-15 实机补）：商铺首页 block2 壳挂的是
        `buildStringToimg2.php?t_page=y_left&phone=<token>&fei=1&en_corpid=<每店一份的密文>`，
        站方按 en_corpid 判店，重建 URL 只会把这两个参数丢掉、换回 0 字节正文——通路账上的
        「码图 0 次」有一半是这么来的。取不到 src（token 只出现在 JS 字符串里）时回空串，
        由 `grab` 退回旧的 IMG_URL 重建法。

        为何必须做归属这一步（实机串号取证，2026-11-04）：
          长沙东方工业泵厂(csdfgyb123) 与 智恒厦门微电子(51chinasensor) 页面里的
          「联 系商家」弹窗(phone_mask_list) 各挂 3 张码图，文案一字不差
          （进口配件 / 进口品牌现货 / 现货微信同步），其中两张识别结果完全相同：
            13918838942、18217311176（都是上海号段）
          而两家各自「手机：」栏里的号才是长沙/厦门号段、与店铺所在地市一致。
          => 那是站方 CPC 推广位的广告主号码（同一个号投到上千家不相关店铺），
             直接页面级 findall 就会把别人的手机号记到本公司头上，外呼永远打不通。

        两条归属判据：
          ① 在「手机/电话/联系电话」标签作用域内 → 本店登记联系方式，直接收；
          ② 其余位置（弹窗「供应商」卡、推广联系电话框等）的码图 → 只有当该处附近
             出现指向本店子域的链接时才收。这样既剔掉广告主号，又保住同一份本店号码的
             另一种字体（pro/y_left 两通道互为冗余：实测有张 y_left 读不出、pro 能读出）。
          两条都要求不在注释块里。
        """
        page = page or ""
        if not page:
            return []
        own = "%s.cn.makepolo.com" % str(slug or "").lower()
        owned = set()
        for seg in _CONTACT_SCOPE_RE.findall(page):
            for style, tok in _CODE_RE.findall(seg):
                owned.add((style, tok))
        # (style, token) → 页面上原样写着的那条 src（同一张号多处出现时保留第一条 img）
        src_of = {}
        for src in _CODE_SRC_RE.findall(page):
            m = _CODE_RE.search(src)
            if m:
                src_of.setdefault((m.group(1), m.group(2)), src)
        out, seen = [], set()
        for m in _CODE_RE.finditer(page):
            key = (m.group(1), m.group(2))
            if key in seen:
                continue                       # 同一张号在页上多处重复，只取一次
            if self._in_comment(page, m.start()):
                continue
            if key not in owned and own not in page[max(0, m.start() - 1500):m.start() + 400].lower():
                continue                       # 既不属本店联系栏，也不在本店子域卡片里 → 推广位
            seen.add(key)
            out.append((key[0], key[1], src_of.get(key, "")))
        return out

    # ── 号码图片码：整店一次批量取，一轮不成再补一轮 ────────────
    def _codes(self, slug, page, shop_url):
        """返回 (手机列表, 固话列表)。成功判据是「响应体是 PNG」而不是状态码：
        0 字节空正文＝站方不放图（这家店没开、或图片服务当前不给）。
        两阶段取法（常量里有实测数据）：先探测前两张，命中就把这一家其余全取；
        探测不中只再补一张——出图是按店的，在不出图的店上多打毫无意义。
        候选列表已经过归属过滤（见 _shop_code_tokens），不会把 CPC 广告主的号读成本店手机。
        `shop_url` 传**这张页面真正来自的那条 URL**：取图要用它做 base 把页面上写着的
        相对 src（`/purchase/buildStringToimg2.php?…&fei=…&en_corpid=…`）拼成绝对地址，
        换成重建的 URL 就会丢参数、拿 0 字节（2026-11-15 实机）。"""
        codes = self._shop_code_tokens(page, slug)   # 同一号码在页上常出现两处，且要排除推广位
        if not codes:
            return [], []
        if self._img_off:
            return [], []
        mobiles, tels = [], []
        got_png = [0]
        img_last = ""          # 最近一张没拿到 PNG 的**原因**（停用那句日志要带上它）

        def grab(style, token, src):
            """返回 None＝这一张没拿到 PNG；返回空串＝拿到了图但读不出可信号码。"""
            nonlocal img_last
            # 页面原样 URL 优先（含 fei/en_corpid）；页上只留了 token、没留 src 的少数形状
            # 才退回旧的整条重建，至少不比原版差
            url = urljoin(shop_url or SHOP_HOST.format(slug=slug), src) if src else \
                IMG_URL.format(slug=slug, style=style, token=token)
            # 图片码是按店的子域取的，熔断账也就按这一台店记（口径见 cdp_base._skip_dead_host）：
            # 连不上与「这家没开图」是两件事，但两者的处置一样——别再为这一家付连接超时
            if self._skip_dead_host(url):
                img_last = "主机已熔断（本轮不再为它付连接超时）"
                return None
            body = b""
            status = 0
            try:
                r = self._session.get(url, timeout=http_timeout(20), headers={"Referer": shop_url})
                self._mark_activity()
                status = r.status_code
                body = r.content or b""
            except Exception as e:
                # 旧写法 `except: body=b""` 把死因整条吞掉：30 家之后停用图片码通路时，
                # 用户只看到「连续 30 家都不放号码图」，而真凶可能是本机 DNS／连不上
                # ——那两条处方完全不同（前者是站方不放，后者是换网络）
                img_last = self._shape_request_error(e)
                return None
            if body[:4] != b"\x89PNG":
                # 深链账：这张码图被拒是**这一家/这条路**的属性，不写通道级 _block_reason
                # （口径见 cdp_base.http_block_reason 的 deep 分支）
                why = self.http_block_reason(status, body[:2000].decode("utf-8", "ignore"),
                                             url, deep=True)
                if why:
                    self._note_deep_block(url, why)
                    img_last = why
                else:
                    img_last = f"HTTP {status}，正文 {len(body)} 字节不是 PNG"
                return None
            got_png[0] += 1
            n = expected_chars(token)
            num, _ = self._ocr.recognize(body, style=style,
                                         chars=n if n in (11, 12, 13) else None)
            return num or ""

        def take(batch):
            hit = False
            for style, token, src in batch:
                self._pace_before_request()
                num = grab(style, token, src)
                if num is None:
                    continue
                hit = True
                self._tally(num, mobiles, tels)
            return hit

        probed = codes[:self.IMG_PROBE_CODES]
        tail = codes[self.IMG_PROBE_CODES:]
        hit = take(probed)
        if not hit and tail:
            # 探测不中：只再要一张（历史上有少数店就是靠后那一张出图）
            hit = take(tail[:self.IMG_PROBE_FALLBACK])
            tail = tail[self.IMG_PROBE_FALLBACK:]
        if hit and tail:
            take(tail)      # 确认这一家放图，剩下的号码一次取齐
        if not got_png[0]:
            # 「出了图但一个号都没读出来」不算空转，只有连一张 PNG 都没拿到才计数
            self._img_dead += 1
            if self._img_dead >= self.IMG_DEAD_STREAK:
                self._img_off = True
                # 带上最后一次死因：「站方不放图」与「本机连不上/被拒」是两条不同的处方，
                # 只说前者会让人去查站点，只说后者会让人去查网络
                self._log("  ⛔ 马可波罗连续 %d 家都不放号码图：本轮停用图片码通路"
                          "（只取明文/子域/邮箱前缀），不再白耗请求；最后一次：%s"
                          % (self._img_dead, img_last or "无明确原因（多为站方不放图）"))
        else:
            self._img_dead = 0
        return mobiles, tels

    def _tally(self, num, mobiles, tels):
        """OCR 结果按形态归列；不可信的一律不进（引擎已过约束解码+置信双闸）。"""
        if not num or is_placeholder_number(num) or num in _FAKE_NUMBERS:
            return
        if PHONE_MOBILE_RE.fullmatch(num) and num not in mobiles:
            mobiles.append(num)
            self.src_count["码图"] += 1
        elif PHONE_TEL_RE.fullmatch(num) and num not in tels:
            tels.append(num)

    # ── 产品详情页：明文手机的正源（归属不自证就整页丢弃）──────
    def _pd_alternates(self, stub, slug, pd):
        """首卡那个产品页没给出明文手机时，换**同店其它产品 id** 再试最多两个。

        实测依据（不是猜的）：
          ① 三页列表 50 家里 24 家（48%）本身就带 2 个以上产品 id，备选就在已解析的
             卡片里，不额外花列表请求；
          ② 产品页与商铺子域是两套开关：实测 昆山冠信(slug=15190159322) 联系页 439
             而产品页 200 并名片手机齐全，也有反过来产品页 439 而联系页 200 的店，
             所以换一条腿确实有机会成。
        只补空缺字段，已有的值不覆盖；归属仍然按本店子域自证（见 _parse_product_page）。"""
        for alt in (stub.get("备选产品ID") or [])[:self.PD_ALT_TRIES]:
            cand = self._parse_product_page(
                self._fetch(PRODUCT_URL.format(pid=alt), enc="utf-8", referer=HOME_URL,
                            deep=True), slug)
            for k, v in (cand or {}).items():
                if not pd.get(k):
                    pd[k] = v
            if pd.get("手机"):
                break
        return pd

    def _shop_product_alternates(self, slug, pd):
        """列表卡那几个产品 id 都没给明文手机时，从**这家自己的产品中心页**再取 id 试。

        实测依据（2026-11-15 留档取证，不是猜的）：
          ① 列表卡带的 id 有相当一部分指向已下架/推广位的产品页——那一跳回 200，
             正文却是同一份 25258 字节的通用壳、连「公司名片」块都没有
             （genesisworks / jndsmygs / chaoqiangqp 三家都是这样；同一时刻同店另一个
             id 却给全量名片，所以问题在 id 不在店）；
          ② 同店 `{slug}.cn.makepolo.com/companyProduct.html` 200 且自家产品成串
             （njyuantai01 一例 20 个 id：/product/101104014473.html…），拿它第一个 id
             去产品页即得 手机 13675360858、联系人、电话、地址全量名片。
        成本闸：这一跳只在「前两条产品页腿都没出货」时才发，一店最多取 CP_PID_TRIES 个 id，
        一轮最多为 CP_SHOP_CAP 家走这条路（超出就只说一次，绝不悄悄烧预算）。
        归属仍然按名片里的子域自证（见 _parse_product_page），不因为 id 来自本店页面就免检。"""
        if self._cp_shops >= self.CP_SHOP_CAP:
            self._leg_warn(f"自家产品中心页这条腿本轮已走完 {self._cp_shops} 家"
                           f"（上限 {self.CP_SHOP_CAP} 家），后面的店不再多打，只走码图")
            return pd
        self._cp_shops += 1
        list_url = SHOP_PRODUCT_LIST_URL.format(slug=slug)
        page = self._fetch(list_url, enc="utf-8",
                           referer=SHOP_HOME_URL.format(slug=slug), deep=True)
        if not page:
            return pd
        pids = list(dict.fromkeys(_CP_PID_RE.findall(page)))
        if not pids:
            # 200 但一个 id 都没列出＝站方把产品中心页也换成 JS 注水了，
            # 这一条腿从此没原料（与「腿通了但这家不给看」是两件事，必须开口说）
            self._leg_warn("产品中心页 200 却没列出一个自家产品 id，"
                           "「自家产品页」这条腿本轮没原料（多半是站方改成 JS 注水）")
            return pd
        for pid in pids[:self.CP_PID_TRIES]:
            cand = self._parse_product_page(
                self._fetch(PRODUCT_URL.format(pid=pid), enc="utf-8", referer=list_url,
                            deep=True), slug)
            for k, v in (cand or {}).items():
                if not pd.get(k):
                    pd[k] = v
            if cand.get("手机"):
                # 出货记在 _cp_hits（渲染成 stats()["cp_leg"]），号码来源仍然算「产品页」那一格：
                # 三条路径取回的是同一种名片，分开计会让一条号码在账上出现两次
                self._cp_hits += 1
                break
        return pd

    def _parse_product_page(self, page, slug):
        """取产品页的「公司名片」与「供应商信息」两块。
        归属闸门：名片里「公司名称」那条链接的子域必须等于本店 slug，不等就整页不要
        （实测 14 个产品页全一致；这道闸是为了站方哪天把名片换成推荐位也不会串号）。
        取不到就回空字典，绝不拿全文正则去扫手机号——那一页里 appid、统一社会信用代码
        和页内跟踪串都能凑出 11 位“合法手机”，扫就是编号。"""
        if not page:
            return {}
        card = _PD_CARD_RE.search(page)
        seg = card.group(1) if card else ""
        own = _PD_OWN_RE.search(seg) if seg else None
        if not own or own.group(1).lower() != str(slug or "").lower():
            return {}
        out = {}
        mo = _PD_MOBILE_RE.search(seg)
        if mo:
            out["手机"] = mo.group(1)
        pe = _PD_PERSON_RE.search(seg)
        if pe:
            out["联系人"] = clean_text(pe.group(1))
        sup = _PD_SUP_RE.search(page)
        sseg = sup.group(1) if sup else ""
        for rex, key in ((_PD_TEL_RE, "电话"), (_PD_FAX_RE, "传真"), (_PD_ADDR_RE, "地址")):
            m = rex.search(sseg)
            if m:
                out[key] = clean_text(m.group(1))
        return out

    # ── 详情：contact_us（UTF-8）→ 明文 + 码图 + 三条免 OCR 通路 ──
    def _refuse_why(self, legs):
        """两条页腿都没拿回正文时，把它们各自的**真实**死因凑成一句话。

        2026-11-15 实机教训：这一句旧写法硬写着「都不给看（403）」，而商铺首页后来回的是
        HTTP 200 + 14KB 火山引擎 WAF 人机验证壳——照那句去查路径的人永远查不到真凶。
        原因优先取深链账里那一句（`http_block_reason` 真看过响应体，比状态码懂得多），
        账上没有才退回状态码，两个都没有就说「空响应」，绝不编一个原因上去。
        ⚠ 取值必须**按 URL** 查（`_deep_block_why`），不能按主机查：同一家主机下
        contact_us.html 的 403 和商铺首页的 200 质询壳是两条不同的死因，主机账只留最后一条，
        拿它配状态码就会给 403 那一腿印上壳的原因（2026-11-16 实机就是这个假话）。"""
        seg = []
        for name, u, code in legs:
            if not u:
                continue
            why = self._deep_block_why(u)
            if not code:
                seg.append(f"{name}空响应")
            elif why.startswith(f"HTTP {code}") or why.startswith(f"站点返回 HTTP {code}"):
                seg.append(f"{name} {why}")          # 状态码级被拒：那句话里已经带码了
            elif why:
                seg.append(f"{name} HTTP {code}（{self._why_short(why)}）")
            else:
                seg.append(f"{name} HTTP {code}")
        return "、".join(seg) or "两条页腿都没发出请求"

    def crawl_detail(self, stub):
        rec = self._base_rec(stub)
        slug = stub.get("公司ID") or stub.get("memberId") or ""
        if not slug:
            rec["状态"] = "详情页导航失败"
            return rec
        url = SHOP_URL.format(slug=slug)
        rec["详情URL"] = url
        # 先取产品详情页（列表卡就带 proid）：明文手机、座机、传真都写在那里
        pid = str(stub.get("产品ID") or "")
        self._last_status = 0        # 没有 proid 就根本不发这一跳，清掉上一家的残留值
        pd = self._parse_product_page(
            self._fetch(PRODUCT_URL.format(pid=pid), enc="utf-8", referer=HOME_URL,
                        deep=True) if pid else "",
            slug)
        prod_status = self._last_status
        page = self._fetch(url, enc="utf-8", referer=stub.get("公司网址", HOME_URL), deep=True)
        shop_status = self._last_status
        # 联系页不给看时换**商铺首页**这条腿（2026-11-15 实机：contact_us 大面积 403，
        # 而同期商铺首页基本全 200）。这一跳只在联系页空了才发，所以放行正常的店不会多花请求；
        # 状态码取这一跳自己的（商铺首页 439 同样是「整店不给看」）。
        # 旧注释在这里写着「首页与联系页字段壳同构、_FIELD_RE 原样能用」——那句是错的，
        # 首页是 block2 壳（标签带 class、值落在下一条 span、码图是 buildStringToimg2.php），
        # 判据没跟上时这一跳拿回 44KB 却读不出一个字段，等于白取（取证与修法见 SHOP_HOME_URL 上方）。
        home_status = 0
        home = ""
        page_url = url          # `page` 真正来自哪条 URL（取码图时要拿它当 base 拼相对 src）
        if not page:
            home = SHOP_HOME_URL.format(slug=slug)
            alt = self._fetch(home, enc="utf-8", referer=HOME_URL, deep=True)
            home_status = self._last_status
            if alt:
                # 这句在换状态码**之前**打：要说的是「联系页那一跳为什么没用」，
                # 换成商铺首页的 200 就成了废话。原因走 `_refuse_why`（旧写法只印状态码，
                # 商铺首页回 200 + 验证壳那一档就会打成「联系页没给到（200）」，读的人一头雾水）
                why = self._refuse_why((("联系页", url, shop_status),))
                self._log(f"  · 改用商铺首页取字段：{why}")
                page, shop_status, page_url = alt, self._last_status, home
                rec["详情URL"] = home
        # 分档证据只取这一家自己的那一页（联系页或商铺首页）：产品页是全站推荐位，
        # 号码可能是别家公司的，拿它当「这一页到底给不给号」的证据就是说谎（同 _keep_detail）
        self._keep_detail(page)
        # 首卡那个产品页没给明文手机：先换同店其它产品页再试（比图片码便宜且更可信，
        # 而且拿到底下就不用花那几个取图请求）。只有产品页那一跳没给手机的店才会多打这 1~2 下。
        if not pd.get("手机"):
            pd = self._pd_alternates(stub, slug, pd)
        # 列表卡那几个 id 还是没给手机：换**这家自己的产品中心页**上的 id（第三条腿，理由与
        # 实测数据见 _shop_product_alternates 的 docstring）。仍然没给才去花图片码的请求。
        if not pd.get("手机"):
            pd = self._shop_product_alternates(slug, pd)
        if not page and not pd:
            # 两页都空才算没拿到数据。实测有商铺页直接回 439（shop135637108916827 就是）而
            # 产品页名片完整的店：拿产品页那一跳的明文手机照样能成单，不该整条丢掉。
            #
            # 而「两页都空」在实测里几乎全是站方对**整家商铺**回 439（2026-11-04 取证：
            # 四家这样的店，首页/联系页/产品列表/产品页全 439，同机同期别家 200，
            # 而且隔天还是 439）——那是商铺本身在站上已下架，不是风控在拦，所以给
            # 单独状态：① 用户日志里看得懂；② 它会进种子库（重试两次后锁定），
            # 不会像「导航失败」那样每轮重新白烧两个请求。真正的网络异常仍归导航失败，
            # 下一轮重采。
            dead = (prod_status in (404, 439) or shop_status in (404, 439)
                    or home_status in (404, 439))
            # 「两条页腿都没正文」和 439 不一样，**不**并进 dead：403 在本站是**按路径**
            # 生效的（同一时刻 contact_us 大面积 403、商铺首页 12/12 全 200），按店锁进
            # 种子库等于用一条路径的开关去判一整家商铺下架，误伤不可逆。让它留在
            # 「导航失败」里下一轮重采，代价只是每轮多两个请求。
            # 这一句从「只在 403 时才说」放宽成「凡是判成可重试就说一次」：2026-11-15 实机里
            # 商铺首页回 200 + WAF 验证壳时也判可重试，但状态码不是 403、旧条件不成立，
            # 于是账上只剩一句干巴巴的「导航失败」，没人知道那一页其实是站方在做人机质询。
            if not dead:
                why = self._refuse_why((("联系页", url, shop_status),
                                        ("商铺首页", home, home_status)))
                self._log(f"  · 两条页腿都没拿到正文（{why}），按可重试处理（不锁库）")
            rec["状态"] = "商铺已关闭(站方不给看)" if dead else "详情页导航失败"
            return rec
        fields = {}
        # 先剥 HTML 注释再取字段：商铺首页在码图旁边并排写着 `<!-- <em class=
        # "show_sub_cpc_fixphone">025-****1400</em>-->` 这种「站方自己掩好位等填」的残留
        # （实测那串掩码与产品页明文 025-85551400 对得上，说明确实是本店那一栏）。
        # 值跨到下一条 span 的判据一放宽，这种注释就会被读进字段里——页面上根本不展示的东西
        # 不能当联系方式（与 _in_comment 处置码图同一条理由）。
        for k, v in _FIELD_RE.findall(_COMMENT_RE.sub(" ", page or "")):
            v = clean_text(v)      # 实体残留/标签/正规实体/空白一并收拢（见 clean_text）
            # 码图占位（A/B 两型与首页 block2 型都把 <img> 夹在字段里）对明文通路没有意义，
            # 整段摘掉：`_CODE_RE` 的字符类在 & 处就停了，只摘前半段会把 `fei=1&en_corpid=…`
            # 留在文本里，看着就像「这一栏有值」
            v = _CODE_STRIP_RE.sub("", v).strip(" ：:|/")
            if v and (k not in fields or not fields[k]):
                fields[k] = v[:120]
        meta = _META_RE.search(page)
        mt = _htmlmod.unescape(meta.group(1)) if meta else ""

        # 联系人：字段优先，产品页名片次之（名片里的「联系人」是站方结构化字段），meta 兜底
        person = fields.get("联系人", "") or pd.get("联系人", "")
        if not person and mt:
            mm = re.search(r"联系人[:：]\s*([^、，,]{2,20})", mt)
            person = clean_text(mm.group(1)) if mm else ""
        # 先把「产品词＋姓＋称谓」里的产品词切掉，再交给基类拆名字/岗位（否则前缀会跟着名字入库）
        cname, cpos = parse_contact_field(clean_contact_person(person))
        if cname:
            rec["联系人"] = cname
        pos = compose_position("", "" if HONORIFIC_ONLY_RE.match(cpos or "") else cpos)
        if pos:
            rec["岗位"] = pos[:20]
        if not cname and not cpos:
            d = dept_only(person)
            if d:
                rec["岗位"] = d[:20]

        # 邮箱：字段优先，meta 兜底；剔站方域名（service@makepolo.com 是客服不是客户）
        mail_src = fields.get("Email") or fields.get("邮箱") or ""
        if not mail_src and mt:
            me = re.search(r"([\w.\-+]+@[\w.\-]+\.\w{2,})", mt)
            mail_src = me.group(1) if me else ""
        em = EMAIL_RE.search(mail_src)
        if em and not any(d in em.group(0).lower() for d in _FAKE_MAILS) \
                and not is_bad_email(em.group(0)):
            rec["邮箱"] = em.group(0)

        # 传真单独记：它是「看起来像电话但不能外呼」的头号来源，两个页面的传真并起来看
        # （产品页实测会把传真单列一行，与座机只差最后一位）
        fax = re.sub(r"\s+", "", fields.get("传真", "") or pd.get("传真", ""))
        fax_digits = re.sub(r"\D", "", fax)

        mobiles, tels = [], []
        # 通路①（实测最硬的一条）：产品页「公司名片」里的明文手机，归属已按本店子域自证
        pdm = re.sub(r"\D", "", pd.get("手机", ""))
        if len(pdm) == 11 and PHONE_MOBILE_RE.fullmatch(pdm) \
                and pdm not in _FAKE_NUMBERS and not is_placeholder_number(pdm):
            mobiles.append(pdm)
            self.src_count["产品页"] += 1
        pdt = strip_country_code(pd.get("电话", ""))
        if pdt and PHONE_TEL_RE.fullmatch(pdt) and pdt not in tels \
                and re.sub(r"\D", "", pdt) != fax_digits:
            tels.append(pdt)
            self.src_count["产品页"] += 1
        # 产品页没给手机才花图片码的请求（一店最多探测两张 + 补一张）：
        # 产品页覆盖率高且一跳到号，把它当首选是为了少打图片接口——少打就是提速＋防封。
        # 传 `page_url`（这张页面真正来自的那条 URL）而不是固定 contact_us：页面上的码图
        # 是相对地址，base 取错一跳，fei/en_corpid 就跟着一起错，站方回 0 字节。
        if not mobiles:
            cm, ct = self._codes(slug, page, page_url)
            mobiles += cm
            tels += ct
        tels = [t for t in tels if not (fax_digits and re.sub(r"\D", "", t) == fax_digits)]

        # 通路②：商铺子域本身就是手机号（商家拿手机号当店铺名注册）
        if PHONE_MOBILE_RE.fullmatch(slug) and slug not in mobiles:
            mobiles.insert(0, slug)
            self.src_count["子域"] += 1
        # 通路③：运营商手机邮箱前缀（139.com/189.cn/wo.cn 按号段分配，前缀即本机号）
        mm = re.match(r"^(1[3-9]\d{9})@([\w.\-]+)$", (mail_src or "").strip())
        if mm and any(d in mm.group(2).lower() for d in _CARRIER_MAIL) \
                and mm.group(1) not in mobiles:
            mobiles.append(mm.group(1))
            self.src_count["邮箱前缀"] += 1
        # 通路④：少数店把手机/电话直接写成明文（注意别把传真列当电话）
        for k in ("手机", "电话", "联系电话"):
            for v in re.split(r"[/、,;\s]+", fields.get(k, "")):
                v = strip_country_code(v)
                if not v or is_placeholder_number(v) or v in _FAKE_NUMBERS:
                    continue
                if PHONE_MOBILE_RE.fullmatch(v) and v not in mobiles:
                    mobiles.append(v)
                    self.src_count["明文"] += 1
                elif PHONE_TEL_RE.fullmatch(v) and v not in tels \
                        and re.sub(r"\D", "", v) != fax_digits:
                    tels.append(v)
                    self.src_count["明文"] += 1

        # 跨店公共号哨兵：归属过滤以外的第二道闸，拦住那些 DOM 语义没过滤干净的
        # 推广号/中介号（同一号第 3 家店起不入库）
        mobiles = [m for m in mobiles if not self._public_num_hit(m, slug)]
        tels = [t for t in tels if not self._public_num_hit(t, slug)]

        # 假区号的固话整串剔掉（实测「0193-52178592」是商家把手机 19352178592 的前缀当区号写）：
        # 形状过了 PHONE_TEL_RE，但 0193 不在真实长途区号树里，外呼必打不通。宁缺不假——
        # 手机号本来就在自己那一列，剔掉这列假固话不损失联系方式。
        bad_tels = [t for t in tels if not is_valid_landline(t)]
        if bad_tels:
            self._log(f"   ⚠ 剔掉假区号固话 {'、'.join(bad_tels)}（区号不在号码树里，外呼打不通）")
        tels = [t for t in tels if is_valid_landline(t)]

        if mobiles:
            rec["手机"] = " / ".join(mobiles[:3])
        if tels:
            rec["电话"] = " / ".join(tels[:2])

        addr = fields.get("公司地址") or fields.get("地址") or ""
        if not addr and mt:
            ma = re.search(r"地址[:：]\s*([^、，,]{6,80})", mt)
            addr = ma.group(1).strip() if ma else ""
        if not addr:
            addr = pd.get("地址", "")      # 产品页只写到镇/路一级，所以排最后
        addr = clean_address(re.sub(r"^\s*中国\s*", "中国 ", addr))
        if addr:
            rec["地址"] = addr
        if not rec.get("主营产品"):
            rec["主营产品"] = stub.get("主营产品", "")[:200]

        if not self.detail_relevant(rec):
            rec["状态"] = "类目不符(已过滤)"
            return rec
        has_phone = bool(rec.get("电话") or rec.get("手机"))
        rec["状态"] = ("成功" if (has_phone or rec.get("邮箱"))
                       else ("无电话(未公开)" if rec.get("地址") or rec.get("公司网址") else "无联系方式"))
        return rec
