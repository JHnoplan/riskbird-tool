"""生成带内嵌截图的图文新手指南 .docx（标准 OOXML，无需第三方库）。"""
import os
import struct
import zipfile

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(OUT_DIR, "guide_images")

XML_NS = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
          'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
          'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">')

MAX_W_EMU = int(6.3 * 914400)   # 版心宽
MAX_H_EMU = int(4.6 * 914400)   # 单图最大高
EMU = 914400


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Builder:
    def __init__(self):
        self.body = []
        self.pics = []  # (rid, filename, emu_w, emu_h)
        self.rid = 100

    def para(self, text, size=21, bold=False, color=None, shade=None, align=None):
        rpr = f'<w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="PingFang SC"/><w:sz w:val="{size}"/>' \
              f'<w:szCs w:val="{size}"/>'
        if bold:
            rpr += "<w:b/>"
        if color:
            rpr += f'<w:color w:val="{color}"/>'
        rpr += "</w:rPr>"
        ppr = "<w:pPr>"
        if shade:
            ppr += f'<w:shd w:val="clear" w:fill="{shade}"/>'
        if align:
            ppr += f'<w:jc w:val="{align}"/>'
        ppr += '<w:spacing w:before="60" w:after="60"/></w:pPr>'
        self.body.append(f"<w:p>{ppr}<w:r>{rpr}<w:t>{esc(text)}</w:t></w:r></w:p>")

    def h1(self, text):
        self.para(text, size=40, bold=True, color="1F4E8C", align="center")

    def h2(self, text):
        self.para(text, size=28, bold=True, color="1F4E8C")
        # 蓝色下边框线
        self.body[-1] = self.body[-1].replace(
            '<w:spacing w:before="60" w:after="60"/></w:pPr>',
            '<w:pBdr><w:bottom w:val="single" w:sz="12" w:color="1F4E8C"/></w:pBdr>'
            '<w:spacing w:before="240" w:after="120"/></w:pPr>')

    def h3(self, text):
        self.para(text, size=24, bold=True, color="C0392B")

    def tip(self, lines):
        for i, ln in enumerate(lines):
            self.para(ln, size=20, shade="FFF3CD")

    def picture(self, img_path):
        self.rid += 1
        rid = f"rId{self.rid}"
        fn = os.path.basename(img_path)
        pw, ph = png_size(img_path)
        cw, ch = max(int(pw / 2 * EMU), 1), max(int(ph / 2 * EMU), 1)  # Retina 2x 逻辑尺寸
        scale = min(MAX_W_EMU / cw, MAX_H_EMU / ch, 1.0)
        dw, dh = int(cw * scale), int(ch * scale)
        self.pics.append((rid, fn, dw, dh))
        did = self.rid
        drawing = (
            '<w:p><w:r><w:drawing><wp:inline>'
            f'<wp:extent cx="{dw}" cy="{dh}"/><wp:docPr id="{did}" name="{esc(fn)}"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr>'
            f'<pic:cNvPr id="{did}" name="{esc(fn)}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
            f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr>'
            f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{dw}" cy="{dh}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')
        self.body.append(drawing)

    def table(self, header, rows, widths):
        total = sum(widths)
        grid = "".join(f'<w:gridCol w:dxa="{int(w / total * 9360)}"/>' for w in widths)
        tbl = [f'<w:tbl><w:tblPr><w:tblBorders>'
               + "".join(f'<w:{s} w:val="single" w:sz="6" w:color="999999"/>'
                         for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
               + '</w:tblBorders></w:tblPr><w:tblGrid>' + grid + "</w:tblGrid>"]
        for ri, row in enumerate([header] + rows):
            tr = "<w:tr>"
            for ci, cell in enumerate(row):
                shade = 'EAF1FB' if ri == 0 else ('FFFFFF' if ri % 2 == 0 else 'F7FAFD')
                wval = int(widths[ci] / total * 9360)
                tc = (f'<w:tc><w:tcPr><w:tcW w:dxa="{wval}" w:type="dxa"/>'
                      f'<w:shd w:val="clear" w:fill="{shade}"/></w:tcPr>')
                for j, ln in enumerate(cell.split("\n")):
                    tc += f'<w:p><w:pPr><w:spacing w:before="30" w:after="30"/></w:pPr>' \
                          f'<w:r><w:rPr><w:rFonts w:eastAsia="PingFang SC"/>' \
                          f'<w:sz w:val="20"/><w:b w:val="{"on" if ri == 0 else "off"}"/></w:rPr>' \
                          f'<w:t>{esc(ln)}</w:t></w:r></w:p>'
                tc += "</w:tc>"
                tr += tc
            tr += "</w:tr>"
            tbl.append(tr)
        tbl.append("</w:tbl><w:p/>")
        self.body.append("".join(tbl))

    def xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + XML_NS
                + "<w:body>" + "".join(self.body)
                + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                  '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
                + "</w:body></w:document>")


def build_docx(b, out_path):
    ct_parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="png" ContentType="image/png"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                "</Types>"]
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 "</Relationships>")
    doc_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    for rid, fn, _, _ in b.pics:
        doc_rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{fn}"/>')
    doc_rels.append("</Relationships>")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Calibri" w:eastAsia="PingFang SC" w:hAnsi="Calibri"/>'
              '<w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="on" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              "</w:styles>")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct_parts))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", b.xml())
        z.writestr("word/_rels/document.xml.rels", "".join(doc_rels))
        z.writestr("word/styles.xml", styles)
        for _, fn, _, _ in b.pics:
            z.write(os.path.join(IMG_DIR, fn), f"word/media/{fn}")


# ── 文档内容（与图文 HTML 一致）──
b = Builder()
b.h1("企业数据采集工具 · 图文使用说明")
b.para("（看不懂电脑术语也没关系，照着下面的图一步一步点就行）", size=20, color="888888", align="center")

b.h2("一、这个工具能帮你做什么？")
b.para("你只要准备一份写有【公司名称】的表格文件（Excel），打开工具，它会自动帮你在网上（风鸟、企知道、爱企查三个网站）"
       "一家一家地查，把每家公司的【老板姓名、注册资金、电话、邮箱、地址、经营范围】等信息自动抄下来，最后整理成表格给你。"
       "导出时还会额外生成一份已去重、分好栏的干净数据，直接导入客户库。")
b.para("你给一份公司名单 → 它自动帮你查全资料 → 再还你一份填满资料的表格。", size=24, bold=True, color="1F4E8C")

b.h2("二、用之前，先准备好这 3 样（只需准备一次）")
b.table(["要准备什么", "怎么做"],
        [["1. 谷歌浏览器\n（Google Chrome）", "电脑上装了就行。没装的话找 IT 或在网上下载安装。"],
         ["2. 工具图标\n（RiskBirdTool.exe）", "双击它就能打开。如果弹出蓝色提示框，点\u201c更多信息\u201d，再点\u201c仍要执行\u201d。"],
         ["3. 网站账号\n（要用哪个登录哪个）", "第一次用时在浏览器里登录一下，登录过一次就记住了，以后不用再登。"]],
        [3, 5])

b.h2("三、工具长这样（先认识一下界面）")
b.para("打开后你会看到下面这个界面，一共分 4 个区域：①最上面选网站和登录 ②中间放文件 ③下面控制开始停止 ④最下面是运行记录。")
b.picture(os.path.join(IMG_DIR, "overview.png"))

b.h2("四、正式开始：跟着图做 5 步")

b.h3("第 1 步：选一个网站")
b.para("点界面红框①那个\u201c数据来源\u201d下拉框，从 风鸟 / 企知道 / 爱企查 里选一个你想用的网站。")
b.picture(os.path.join(IMG_DIR, "step1_source.png"))

b.h3("第 2 步：登录（只有第一次需要）")
b.para("点红框②的\u201c打开网页\u201d按钮，浏览器会弹出那个网站。你像平时一样输入账号密码登录。")
b.para("登录成功后，红框处的\u201c未登录\u201d会变成\u201c已登录\u201d。以后就不用再登了。")
b.picture(os.path.join(IMG_DIR, "step2_login.png"))

b.h3("第 3 步：把你的公司表格放进去")
b.para("点红框③的\u201c选择文件（可多选）\u201d按钮，找到你电脑上那份写着公司名字的表格，选中它（可以一次选好多个）。")
b.picture(os.path.join(IMG_DIR, "step3_file.png"))
b.tip(["工具会自己认出哪一列是公司名字，不用你管。",
       "万一它拿不准，会弹出下面这个小窗口问你\u201c公司名在哪一列？\u201d——红框圈出来的就是它猜最准的那一列，"
       "你确认没问题就点\u201c确定\u201d；不对就勾正确那一行再点\u201c确定\u201d。",
       "勾上\u201c记住选择\u201d，下次同一种表格就不会再问你啦。"])
b.picture(os.path.join(IMG_DIR, "dialog_column.png"))

b.h3("第 4 步：开始查")
b.para("点\u201c开始采集\u201d（红框4），工具就自动一家一家查了，下面的进度条会走。")
b.para("· a 暂停：中途歇一下，之后点 b 恢复 接着查。\n"
       "· c 停止：彻底结束。\n"
       "· d 请求间隔：查太快怕被网站限制？把这个数字调大，它就慢一点、稳一点。")
b.picture(os.path.join(IMG_DIR, "step4_start.png"))

b.h3("第 5 步：拿到结果")
b.para("查完后，点绿色的“导出 Excel”按钮（红框5），选个存放位置（比如桌面）。")
b.para("点一下会存两个文件：\n"
       "· 企业数据_全部平台… —— 查到的全部资料（一家公司一行，字段齐全）。\n"
       "· 清洗后客户库入库数据… —— 已按公司去重合并、剔掉查不到的、把手机号/固话/邮箱分栏填好的干净版，可直接导入客户库。")
b.para("两个文件都在你刚选的同一个文件夹里，双击就能用 Excel/WPS 打开。")
b.picture(os.path.join(IMG_DIR, "step5_export.png"))
b.para("小提醒：“清洗后”那份会自动去常见文件夹（桌面、下载、文档）里找你的《客户库数据导入模板.xlsx》对栏位，"
       "模板列改了也能自适应；万一没找到模板，会按默认列头生成，不影响使用。", size=20, color="666666")

b.h2("五、查到的资料放在哪里？会不会丢？")
b.para("放心，不会丢。你不用手动保存。")
b.para("工具一边查，会一边自动把结果存到你电脑桌面上的一个文件夹里，名字叫\u201c企业数据自动采集工具\u201d，"
       "里面按网站分成 风鸟、企知道、爱企查 三个表格文件。")
b.para("就算你不小心关了工具、或者电脑突然断电，重新打开后再放同一份表格，它会从上次查到的地方接着往下查，不会重复、也不会白干。")

b.h2("六、遇到这几种情况，别慌，是正常的")
b.table(["你看到的现象", "是怎么回事 / 该怎么办"],
        [["爱企查提示\u201c请关闭浏览器的调试窗口\u201d", "是不小心按到了键盘上的 F12 键。再按一次 F12 关掉，或把浏览器关掉重新打开就好。"],
         ["表格里有些格子是空的", "不是工具坏了。是这家公司在网上本来就没有这项资料（比如小公司常常没有官网、邮箱），很正常。"],
         ["爱企查\u201c核准日期\u201d显示星号 ****", "是爱企查对没充值会员的人故意藏起来了，不是工具的问题。"],
         ["某一家公司标了\u201c未找到\u201d", "这家公司名字在网上搜不到（可能改名了），工具会自动跳过查下一家，不影响整体。"],
         ["公司名列没认对 / 想重新选", "看下方“运行日志”会写明识别到第几列；弹了选择窗口就勾对那一列点确定。"],
         ["“清洗后入库数据”里行数比全量那份少", "正常的。它把查不到的公司剔掉了，同一家公司在几个平台都查到时也会合成一行，不会重复入库。"]],
        [3, 5])

b.h2("七、一句话记住整个流程")
b.para("双击打开 → 选网站 → 登录一次 → 放公司表格 → 点开始 → 点导出，同时拿到「全量资料表」和「清洗入库表」。", size=24, bold=True, color="1F4E8C")

out = os.path.join(OUT_DIR, "新手指南.docx")
build_docx(b, out)
print("generated:", out, os.path.getsize(out), "bytes,", len(b.pics), "images embedded")
