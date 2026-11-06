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
from mk_phone_ocr import load_default_font, expected_chars, TOKEN_LEN_OFFSET
import province_filter as _pf

HOME_URL = "http://cn.makepolo.com/"
SEARCH_URL = "http://caigou.makepolo.com/spc_new.php?search_flag=2&q={kw}&pg={page}"
SHOP_URL = "http://{slug}.cn.makepolo.com/contact_us.html"
IMG_URL = "http://{slug}.cn.makepolo.com/purchase/buildStringToimg.php?t_page={style}&phone={token}"
# 产品详情页（站方主域）：实测这一页把**手机号明文**写在「公司名片」里，而商铺页 contact_us
# 把同一个号藏成图片码。列表卡就带 proid，所以这一页是一跳可达的免费明文源。
PRODUCT_URL = "https://china.makepolo.com/product-detail/{pid}.html"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 产品卡：区域 + 公司名 + 商铺子域 + 主营（三链接并列：首页 / companyProduct / contact_us）
_CARD_RE = re.compile(
    r'class="s_product_contact[^"]*">(.*?)</div>', re.S)
_CITY_RE = re.compile(r"\[([\u4e00-\u9fa5A-Za-z ]{2,20})\]")
_SHOP_RE = re.compile(r"http://([\w\-]+)\.cn\.makepolo\.com/")
_NAME_RE = re.compile(r'cn\.makepolo\.com/"[^>]*>\s*([^<]{4,60}?)\s*</a>')
_PROD_RE = re.compile(r'companyProduct\.html"[^>]*>\s*([^<]{4,300}?)\s*</a>')
# 列表卡里就带产品 id（contact_us.html?proid=…）：拿着它就能一跳跳到写了明文手机的产品页
_PROID_RE = re.compile(r'(?:contact_us\.html\?proid=|product-detail/)(\d{9,14})')
# 号码图片码：通道名(t_page) 与密文 token 都在 URL 里，token 长度直接给出号码字符数
_CODE_RE = re.compile(r'buildStringToimg\.php\?t_page=(\w+)&phone=([^"\'\s&]+)')
# 【码图归属本店判据①】挂在「手机/电话/联系电话」标签作用域里的码图＝本店登记联系方式。
# 用字段语义而不是区块类名划线，是因为实测里同一份号码会同时出现在好几个容器
# （connect_msg 的「手机：」、弹窗「供应商：」、cont_num_box2 的「联系电话」框），
# 而推广块 cont_num_box2 装的偏偏也可能是本店号——按类名黑白名单必错。
_CONTACT_SCOPE_RE = re.compile(
    r"(?:<span>|<li>)\s*(?:手机|电话|联系电话)\s*[：:]\s*(.*?)\s*(?:</span>|</li>)", re.S)
# 明文字段：A 型在 <span>、B 型在 <li>，一并抓（标签词按两种模板的写法列全）
_FIELD_RE = re.compile(r"(?:<span>|<li>)\s*(联系人|联系电话|电话|手机|传真|Email|邮箱|"
                       r"公司地址|地址|邮政编码|QQ)\s*[：:]\s*(.*?)\s*(?:</span>|</li>)", re.S)
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
        # 号码通路账目（结束日志用，让用户看见「手机号是从哪几条路拿到的」）
        self.src_count = {"产品页": 0, "码图": 0, "子域": 0, "邮箱前缀": 0, "明文": 0}

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
        return True

    # ── 旁路浏览器：本站不占用标签 ────────────────────────────
    def start_browser(self, headed=True):
        return True

    def ensure_tab(self, log=None):
        return True

    def close_browser(self):
        return None

    def _is_session_alive(self):
        return True

    def probe_block(self):
        """恢复探测：真取一页搜索并解析得出公司卡才算解除（限流薄壳也是 200）。"""
        url = self._block_probe_url or self.build_search_url("公司", 1)
        try:
            r = self._session.get(url, timeout=25, headers={"Referer": HOME_URL})
            if r.status_code == 200 and not self.http_block_reason(r.status_code, r.text):
                if self.parse_list(self._decode(r, "gb18030"), "公司"):
                    self._block_reason = ""
                    return ""
        except Exception:
            pass
        return self._block_reason or "马可波罗仍在限流状态"

    @staticmethod
    def _decode(r, enc):
        """编码分域：搜索域 gbk/gb18030，商铺子域 utf-8（实测两者不同，解错就整页乱码）。"""
        try:
            return r.content.decode(enc, "ignore")
        except Exception:
            return r.text or ""

    def _fetch(self, url, enc="utf-8", tries=3, guarded=True, referer=""):
        last = ""
        self._last_status = 0
        for i in range(max(1, tries)):
            try:
                self._pace_before_request()
                r = self._session.get(url, timeout=25,
                                      headers={"Referer": referer} if referer else None)
                self._mark_activity()
                self._last_status = r.status_code
                text = self._decode(r, enc)
                block = self.http_block_reason(r.status_code, text, url, r.headers)
                if block:
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
                last = str(e).splitlines()[0][:80] or "连接被重置"
                self._session.close()
                self._session = self._new_session()
            except Exception as e:
                last = str(e).splitlines()[0][:80]
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
        if guarded and self._block_reason:
            if self.handle_http_block(url):
                self._block_reason = ""
                return self._fetch(url, enc=enc, tries=tries, guarded=False, referer=referer)
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
        for chunk in _CARD_RE.findall(html or ""):
            sm = _SHOP_RE.search(chunk)
            if not sm:
                continue
            slug = sm.group(1).lower()
            pidm = _PROID_RE.search(chunk)
            pm = _PROD_RE.search(chunk)
            prod = clean_text(pm.group(1)) if pm else ""
            if slug in idx:
                e = out[idx[slug]]
                alt = pidm.group(1) if pidm else ""
                if alt and alt != e["产品ID"] and alt not in e["备选产品ID"]:
                    e["备选产品ID"].append(alt)
                if prod and prod not in e["主营全文"]:
                    e["主营全文"] = (e["主营全文"] + " " + prod)[:600]
                continue
            nm = _NAME_RE.search(chunk)
            name = clean_text(nm.group(1)) if nm else ""
            if len(name) < 4:
                continue
            if any(x in name for x in ("首页", "登录", "注册", "更多", "下一页")):
                continue
            cm = _CITY_RE.search(chunk)
            idx[slug] = len(out)
            out.append({
                "公司名称": name[:60],
                "公司ID": slug,
                "memberId": slug,
                "产品ID": pidm.group(1) if pidm else "",
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
                self._log(f"  「{kw}」第 {page} 页没取到页面（仍在限流里或会话失效），停止翻页")
                return
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                empty += 1
                if empty >= 2:
                    self._log(f"  「{kw}」第 {page - 1} 页起无公司结果，停止翻页")
                    return
                continue
            empty = 0
            new = skipped = 0
            for e in entries:
                mid = e["公司ID"]
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e["公司名称"])
                nkey = f"{self.PLATFORM_SLUG}:name:{name}"
                if key in self._seen_members or nkey in self._seen_members \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    continue
                # 早过滤：产品卡上的公司名/主营与关键词无关的，连联系方式页都不进（省请求＝提速＋防封）
                # 砍掉的**只按「关键词#主体」标记**，不占全局已见：相关性是「对这个关键词」的判定，同一家对 A 词不符、对 B 词可能正是目标（实机砍掉的名单里就有真同行）。
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._early_skipped += 1
                    self._seen_members.add(f"{self.PLATFORM_SLUG}:{kw}#{mid}")
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e["公司名称"], "memberId": mid})
                    continue
                self._seen_members.add(key)
                self._seen_members.add(nkey)
                e["关键词"] = kw
                new += 1
                yield e
            tail = f"，早过滤 {skipped} 家(未进详情)" if skipped else ""
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
        """返回本页属于本店的码图 [(style, token)]，按页面出现顺序去重。

        为何必须做这一步（实机串号取证，2026-11-04）：
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
            out.append(key)
        return out

    # ── 号码图片码：整店一次批量取，一轮不成再补一轮 ────────────
    def _codes(self, slug, page, shop_url):
        """返回 (手机列表, 固话列表)。成功判据是「响应体是 PNG」而不是状态码：
        0 字节空正文＝站方不放图（这家店没开、或图片服务当前不给）。
        两阶段取法（常量里有实测数据）：先探测前两张，命中就把这一家其余全取；
        探测不中只再补一张——出图是按店的，在不出图的店上多打毫无意义。
        候选列表已经过归属过滤（见 _shop_code_tokens），不会把 CPC 广告主的号读成本店手机。"""
        codes = self._shop_code_tokens(page, slug)   # 同一号码在页上常出现两处，且要排除推广位
        if not codes:
            return [], []
        if self._img_off:
            return [], []
        mobiles, tels = [], []
        got_png = [0]

        def grab(style, token):
            """返回 None＝这一张没拿到 PNG；返回空串＝拿到了图但读不出可信号码。"""
            url = IMG_URL.format(slug=slug, style=style, token=token)
            body = b""
            try:
                r = self._session.get(url, timeout=20, headers={"Referer": shop_url})
                self._mark_activity()
                body = r.content or b""
            except Exception:
                body = b""
            if body[:4] != b"\x89PNG":
                return None
            got_png[0] += 1
            n = expected_chars(token)
            num, _ = self._ocr.recognize(body, style=style,
                                         chars=n if n in (11, 12, 13) else None)
            return num or ""

        def take(batch):
            hit = False
            for style, token in batch:
                self._pace_before_request()
                num = grab(style, token)
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
                self._log("  ⛔ 马可波罗连续 %d 家都不放号码图：本轮停用图片码通路"
                          "（只取明文/子域/邮箱前缀），不再白耗请求" % self._img_dead)
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
                self._fetch(PRODUCT_URL.format(pid=alt), enc="utf-8", referer=HOME_URL), slug)
            for k, v in (cand or {}).items():
                if not pd.get(k):
                    pd[k] = v
            if pd.get("手机"):
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
            self._fetch(PRODUCT_URL.format(pid=pid), enc="utf-8", referer=HOME_URL) if pid else "",
            slug)
        prod_status = self._last_status
        page = self._fetch(url, enc="utf-8", referer=stub.get("公司网址", HOME_URL))
        shop_status = self._last_status
        # 首卡那个产品页没给明文手机：先换同店其它产品页再试（比图片码便宜且更可信，
        # 而且拿到底下就不用花那几个取图请求）。只有产品页那一跳没给手机的店才会多打这 1~2 下。
        if not pd.get("手机"):
            pd = self._pd_alternates(stub, slug, pd)
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
            dead = (prod_status in (404, 439) or shop_status in (404, 439))
            rec["状态"] = "商铺已关闭(站方不给看)" if dead else "详情页导航失败"
            return rec
        fields = {}
        for k, v in _FIELD_RE.findall(page):
            v = clean_text(v)      # 实体残留/标签/正规实体/空白一并收拢（见 clean_text）
            # 码图占位（A/B 两型都把 <img> 夹在字段里）对明文通路没有意义，直接丢掉
            v = _CODE_RE.sub("", v).strip(" ：:|/")
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
        # 产品页覆盖率高且一跳到号，把它当首选是为了少打图片接口——少打就是提速＋防封
        if not mobiles:
            cm, ct = self._codes(slug, page, url)
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
