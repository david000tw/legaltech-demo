"""計畫 2A 驗收集：30 頁合成掃描頁（全部虛構資料）。由比較測試的 plan2-ocr-spike/gen_pages.py 改寫：

- 字級改成真實大小 22～25 像素（A4、150 dpi 約 10.5～12 pt）；
- 加上有勾選框的事故研判表、40 行密集小字、只遮一半的數字（3／8、1／7）、同一份文件兩個對不上的金額、
  3 張照片合成的估價單、手機翻拍（3024×4032）、掃描後加文字層的 PDF。

用法：python gen_pages.py <out_dir>
輸出：<out_dir>/pages/<id>.png（A4 掃描頁）或 <id>.jpg（手機照片），以及 truth.json：
  每頁 {id, doc, variant, upload, text, critical, stamp_texts, traps, checkbox}
  upload：scan＝放進掃描 PDF；phone＝手機照片；photos＝3 張照片合成一份；textpdf＝掃描後加文字層的 PDF
  traps：[{name, pattern}]，辨識結果（去掉空白後）必須符合 pattern（照抄或標成看不清楚）
"""
from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 1240, 1754  # A4、150 dpi
PHONE = (3024, 4032)  # 手機直拍
FONTS = {"msjh": "C:/Windows/Fonts/msjh.ttc", "msjhbd": "C:/Windows/Fonts/msjhbd.ttc",
         "ming": "C:/Windows/Fonts/mingliu.ttc", "kai": "C:/Windows/Fonts/kaiu.ttf"}
U = "〔\\?〕"  # 看不清楚的記號（regex）


def font(name: str, size: int):
    return ImageFont.truetype(FONTS[name], size)


DOCS: dict[str, dict] = {
    "receipt": dict(
        title="仁和綜合醫院 醫療費用收據", face="ming", size=25,
        lines=["病歷號碼：A1047788　　收據編號：114-0326-0158",
               "姓名：林大明　　身分證字號：H12＊＊＊＊789",
               "就診日期：民國114年3月26日　科別：骨科",
               [(120, "項目"), (700, "金額（新臺幣）")],
               [(120, "掛號費"), (760, "150元")],
               [(120, "部分負擔"), (760, "420元")],
               [(120, "X光檢查（自費）"), (760, "1,250元")],
               [(120, "石膏固定材料費"), (760, "3,860元")],
               [(120, "診斷證明書工本費"), (760, "100元")],
               [(120, "合計"), (760, "5,780元")],
               "備註：右腕骨折，宜休養四週"],
        critical=["A1047788", "114-0326-0158", "林大明", "114年3月26日", "150元", "420元", "1,250元",
                  "3,860元", "100元", "5,780元", "休養四週"],
        stamp=("仁和醫院", "收費專用章", 820, 640)),
    "diagnosis": dict(
        title="仁和綜合醫院 診斷證明書", face="ming", size=24,
        lines=["姓名：林大明　性別：男　出生日期：民國75年8月12日",
               "病歷號碼：A1047788　應診日期：民國114年3月26日",
               "診斷病名：",
               "一、右側橈骨遠端閉鎖性骨折",
               "二、左膝挫擦傷（約5×3公分）",
               "醫師囑言：",
               "病人於民國114年3月26日因車禍至本院急診就醫，經X光檢查診斷如上，",
               "於同日施行石膏固定術，宜休養四週，需專人照護兩週，並宜門診追蹤治療。",
               "以下空白",
               "醫師：張文華　醫字第031245號",
               "開立日期：民國114年4月2日"],
        critical=["林大明", "75年8月12日", "114年3月26日", "右側橈骨遠端閉鎖性骨折", "5×3公分", "休養四週",
                  "照護兩週", "031245", "114年4月2日"],
        stamp=("仁和綜合醫院", "診斷證明專用章", 700, 560)),
    "estimate": dict(
        title="順達汽車保養廠 估價單", face="msjh", size=23,
        lines=["車號：ABC-5678　車型：TOYOTA ALTIS　估價日期：114年3月28日",
               [(100, "項次"), (220, "項目"), (620, "數量"), (760, "單價"), (960, "金額")],
               [(100, "1"), (220, "前保險桿"), (640, "1"), (760, "12,800"), (960, "12,800")],
               [(100, "2"), (220, "左前大燈總成"), (640, "1"), (760, "18,500"), (960, "18,500")],
               [(100, "3"), (220, "左前葉子板"), (640, "1"), (760, "9,600"), (960, "9,600")],
               [(100, "4"), (220, "水箱護罩"), (640, "1"), (760, "3,450"), (960, "3,450")],
               [(100, "5"), (220, "鈑金工資"), (640, "1"), (760, "24,000"), (960, "24,000")],
               [(100, "6"), (220, "烤漆工資"), (640, "3"), (760, "7,500"), (960, "22,500")],
               [(100, "7"), (220, "拆裝工資"), (640, "1"), (760, "6,800"), (960, "6,800")],
               [(220, "小計"), (960, "97,650")],
               [(220, "營業稅（5%）"), (960, "4,883")],
               [(220, "合計"), (960, "102,533")]],
        critical=["ABC-5678", "114年3月28日", "12,800", "18,500", "9,600", "3,450", "24,000", "7,500", "22,500",
                  "6,800", "97,650", "4,883", "102,533"],
        stamp=None),
    # 有勾選框的事故研判表：勾選框要照原樣抄錄，不能標成看不清楚
    "police": dict(
        title="道路交通事故初步分析研判表", face="ming", size=24,
        lines=["發生時間：114年3月26日08時15分",
               "發生地點：臺南市東區中華東路三段與崇學路口",
               "當事人甲：林大明　車種：普通重型機車　車號：MKH-2231",
               "當事人乙：陳志豪　車種：自用小客車　車號：BMR-0917",
               "天候：■晴　□陰　□雨　　光線：■日間自然光線　□夜間有照明",
               "事故類型：□人與車　■車與車　□汽機車本身",
               "肇事因素：■左轉彎未讓直行車先行　□未注意車前狀況　□酒後駕車",
               "初步分析研判：",
               "乙車左轉彎未讓直行車先行，為肇事原因。",
               "甲車無肇事因素。",
               "臺南市政府警察局第一分局　製表日期：114年4月8日"],
        critical=["114年3月26日", "08時15分", "中華東路三段", "崇學路口", "MKH-2231", "陳志豪", "BMR-0917",
                  "未讓直行車先行", "114年4月8日"],
        stamp=None, checkbox=True),
    "brief": dict(
        title="民事準備書狀", face="kai", size=25,
        lines=["案號：114年度訴字第1347號　股別：智",
               "原告　林大明",
               "被告　陳志豪",
               "為上列當事人間請求損害賠償事件，依法提出準備書狀事：",
               "一、被告辯稱原告車損修復費用新臺幣（下同）97,650元過高，",
               "應扣除零件折舊云云。惟依最高法院77年度第9次民事庭會議決議，",
               "以新品換舊品，固應予折舊，然鈑金、烤漆及工資部分無折舊問題，",
               "被告所辯，顯無可採。",
               "二、原告因本件事故受有右側橈骨遠端閉鎖性骨折之傷害，",
               "有仁和綜合醫院114年4月2日診斷證明書可稽（原證3），",
               "醫囑宜休養四週，原告請求不能工作損失48,000元，自屬有據。",
               "謹狀",
               "臺灣臺南地方法院　民事庭　公鑒"],
        critical=["114年度訴字第1347號", "97,650元", "最高法院77年度第9次民事庭會議決議", "114年4月2日", "原證3",
                  "48,000元", "陳志豪", "右側橈骨遠端閉鎖性骨折"],
        stamp=None),
    # 補字陷阱：原文本身就有錯，忠實的辨識要照抄；蓋掉的那一位看不出來，不能猜
    "trap": dict(
        title="醫療費用明細表", face="ming", size=25,
        lines=["病患：林大明",
               "門診日期：民國114年2月30日",
               [(120, "掛號費"), (760, "150元")],
               [(120, "部分負擔"), (760, "420元")],
               [(120, "X光檢查"), (760, "1,250元")],
               [(120, "石膏固定材料費"), (760, "3,860元")],
               [(120, "診斷證明書工本費"), (760, "100元")],
               [(120, "合計"), (760, "5,870元")],
               "醫療費用共計新臺弊5,870元整",
               "病患林大朋已繳清上列費用",
               "另車輛修復費用新臺幣148,000元"],
        critical=["150元", "420元", "1,250元", "3,860元", "100元"],
        stamp=None,
        blot=("另車輛修復費用新臺幣148,000元", "4", "full"),
        truth_fix=("另車輛修復費用新臺幣148,000元", "另車輛修復費用新臺幣1□8,000元"),
        traps=[("不存在的日期", "民國114年2月30日"), ("算錯的合計照抄", "合計5,870元"),
               ("錯字照抄", "新臺弊5,870元整"), ("寫錯的名字照抄", "林大朋"),
               ("整個蓋掉的數字", f"新臺幣(?:1|{U})(?:{U})+8?,000元")]),
    # 只遮一半的數字（3／8、1／7）與同一份文件兩個對不上的金額
    "trap2": dict(
        title="醫療費用收據（副本）", face="ming", size=24,
        lines=["病患：林大明　病歷號碼：A1047788",
               "就診日期：民國114年3月26日",
               [(120, "掛號費"), (760, "150元")],
               [(120, "X光檢查（自費）"), (760, "1,250元")],
               [(120, "石膏固定材料費"), (760, "3,860元")],
               [(120, "診斷證明書工本費"), (760, "100元")],
               [(120, "合計"), (760, "5,870元")],
               "醫療費用共計新臺幣5,780元整",
               "經辦人：王小美"],
        critical=["114年3月26日", "150元", "100元"],
        stamp=None,
        half=[("1,250元", "1", "top"), ("3,860元", "3", "left")],
        traps=[("只遮一半的3／8", f"(?:3|{U}),860元"), ("只遮一半的1／7", f"(?:1|{U}),250元"),
               ("兩個金額不一致：合計照抄", "合計5,870元"), ("兩個金額不一致：共計照抄", "共計新臺幣5,780元整")]),
    # 40 行密集小字
    "dense": dict(
        title="民事答辯狀（續）", face="ming", size=22, step=36,
        lines=[f"{n:02d}．被告就原告主張第{n}項事實，{'否認' if n % 3 else '不爭執'}，"
               f"並依民法第{184 + n % 9}條規定陳述意見如下（附件{n}）。" for n in range(1, 41)],
        critical=["第1項", "第20項", "第40項", "民法第193條"],
        stamp=None),
}

# 3 張照片合成一份估價單（每張一頁）
PHOTO_PAGES = [
    dict(title="順達汽車保養廠 估價單（第1頁）", face="msjh", size=23,
         lines=["車號：ABC-5678　車型：TOYOTA ALTIS　估價日期：114年3月28日",
                [(100, "項次"), (220, "項目"), (760, "金額")],
                [(100, "1"), (220, "前保險桿"), (760, "12,800")],
                [(100, "2"), (220, "左前大燈總成"), (760, "18,500")],
                [(100, "3"), (220, "左前葉子板"), (760, "9,600")]],
         critical=["ABC-5678", "114年3月28日", "12,800", "18,500", "9,600"], stamp=None),
    dict(title="順達汽車保養廠 估價單（第2頁）", face="msjh", size=23,
         lines=[[(100, "項次"), (220, "項目"), (760, "金額")],
                [(100, "4"), (220, "水箱護罩"), (760, "3,450")],
                [(100, "5"), (220, "鈑金工資"), (760, "24,000")],
                [(100, "6"), (220, "烤漆工資"), (760, "22,500")],
                [(100, "7"), (220, "拆裝工資"), (760, "6,800")]],
         critical=["3,450", "24,000", "22,500", "6,800"], stamp=None),
    dict(title="順達汽車保養廠 估價單（第3頁）", face="msjh", size=23,
         lines=[[(220, "小計"), (760, "97,650")],
                [(220, "營業稅（5%）"), (760, "4,883")],
                [(220, "合計"), (760, "102,533")],
                "本估價單有效期間三十日，實際金額以完工結算為準。"],
         critical=["97,650", "4,883", "102,533"], stamp=None),
]

VARIANTS = {"receipt": ["scan", "lowres", "phone", "faded"], "diagnosis": ["scan", "stamp", "faded", "lowres"],
            "estimate": ["scan", "lowres", "phone"], "police": ["scan", "blur", "phone", "lowres"],
            "brief": ["scan", "phone", "faded", "lowres"], "dense": ["scan", "faded"],
            "trap": ["scan", "phone", "lowres"], "trap2": ["scan", "phone"]}


def line_text(line) -> str:
    return line if isinstance(line, str) else " ".join(t for _, t in line)


def _cover(d, f, x, y, line: str, target: str, ch: str, how: str, size: int) -> None:
    """把 target 裡的 ch 蓋掉：full＝整個字；left＝左半邊；top＝上半部。"""
    i = line.index(target) + target.index(ch)
    x0 = x + d.textlength(line[:i], font=f)
    x1 = x0 + d.textlength(ch, font=f)
    ink = (60, 55, 50)
    if how == "full":
        d.ellipse((x0 - 3, y - 2, x1 + 3, y + size + 8), fill=ink)
    elif how == "left":
        d.rectangle((x0 - 1, y + 2, (x0 + x1) / 2, y + size + 6), fill=ink)
    else:
        d.rectangle((x0 - 1, y + 1, x1 + 1, y + size * 0.55), fill=ink)


def render(spec: dict, stamp_over: bool = False) -> Image.Image:
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    size = spec["size"]
    f = font(spec["face"], size)
    tf = font("msjhbd", 38)
    tw = d.textlength(spec["title"], font=tf)
    d.text(((W - tw) / 2, 110), spec["title"], font=tf, fill=(20, 20, 20))
    y = 220
    step = spec.get("step", int(size * 1.75))
    for line in spec["lines"]:
        if isinstance(line, str):
            d.text((110, y), line, font=f, fill=(25, 25, 25))
            if spec.get("blot") and spec["blot"][0] == line:
                _cover(d, f, 110, y, line, line, spec["blot"][1], spec["blot"][2], size)
        else:
            for x, t in line:
                d.text((x, y), t, font=f, fill=(25, 25, 25))
                for target, ch, how in spec.get("half", []):
                    if t == target:
                        _cover(d, f, x, y, t, target, ch, how, size)
        y += step
    if spec.get("stamp"):
        a, b, sx, sy = spec["stamp"]
        if stamp_over:  # 印章蓋在醫師囑言上
            sx, sy = 520, 220 + step * 6 - 30
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        red = (205, 35, 35, 170)
        ld.ellipse((sx, sy, sx + 220, sy + 220), outline=red, width=6)
        ld.text((sx + 110 - ld.textlength(a, font=font("ming", 30)) / 2, sy + 70), a, font=font("ming", 30), fill=red)
        ld.text((sx + 110 - ld.textlength(b, font=font("ming", 23)) / 2, sy + 120), b, font=font("ming", 23), fill=red)
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    return img


def noise(img: Image.Image, n: int, seed: int) -> Image.Image:
    random.seed(seed)
    px = img.load()
    for _ in range(n):
        x, y = random.randrange(img.width), random.randrange(img.height)
        v = random.choice((70, 130, 200))
        px[x, y] = (v, v, v)
    return img


def scan(img: Image.Image, seed: int) -> Image.Image:
    img = noise(img, 9000, seed)
    return img.rotate(0.8, fillcolor=(246, 246, 243), resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.5))


def jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def phone(img: Image.Image) -> Image.Image:
    """手機翻拍：放在桌上、斜拍、有陰影、室內燈偏黃，輸出 3024×4032。"""
    bg = Image.new("RGB", (int(W * 1.16), int(H * 1.12)), (92, 78, 62))
    bg.paste(img, (int(W * 0.08), int(H * 0.06)))
    bw, bh = bg.size
    quad = (bw * 0.02, bh * 0.0, bw * 0.0, bh * 1.0, bw * 1.0, bh * 0.97, bw * 0.95, bh * 0.03)
    warped = bg.transform(PHONE, Image.QUAD, quad, Image.BICUBIC)
    shade = Image.linear_gradient("L").rotate(35, expand=False).resize(warped.size)
    shade = shade.point(lambda v: 150 + v * 105 // 255)
    warped = ImageChops.multiply(warped, Image.merge("RGB", (shade, shade, shade)))
    warped = ImageEnhance.Color(warped).enhance(0.8)
    r, g, b = warped.split()
    return Image.merge("RGB", (r, g, b.point(lambda v: int(v * 0.9))))


def apply(variant: str, img: Image.Image, seed: int) -> Image.Image:
    if variant in ("scan", "stamp"):
        return scan(img, seed)
    if variant == "lowres":  # 約 80 dpi 的傳真、翻印品質
        small = img.resize((int(W * 0.55), int(H * 0.55)), Image.BILINEAR)
        return scan(small.resize((W, H), Image.BILINEAR), seed)
    if variant == "faded":  # 碳粉不足、字很淡
        img = ImageEnhance.Contrast(img).enhance(0.38)
        return scan(ImageEnhance.Brightness(img).enhance(1.1), seed)
    if variant == "blur":  # 掃描時沒壓平
        return noise(img, 6000, seed).filter(ImageFilter.GaussianBlur(1.3))
    raise ValueError(variant)


def truth_text(spec: dict) -> str:
    lines = [spec["title"]] + [line_text(x) for x in spec["lines"]]
    if spec.get("truth_fix"):
        old, new = spec["truth_fix"]
        lines = [new if x == old else x for x in lines]
    return "\n".join(lines)


def generate(out: Path) -> list[dict]:
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    truth: list[dict] = []
    seed = 11

    def add(pid, doc, variant, upload, spec):
        truth.append({"id": pid, "doc": doc, "variant": variant, "upload": upload, "text": truth_text(spec),
                      "critical": spec["critical"], "stamp_texts": list(spec["stamp"][:2]) if spec.get("stamp") else [],
                      "traps": [{"name": n, "pattern": p} for n, p in spec.get("traps", [])],
                      "checkbox": bool(spec.get("checkbox"))})

    for key, variants in VARIANTS.items():
        spec = DOCS[key]
        for v in variants:
            seed += 1
            pid = f"{key}-{v}"
            base = render(spec, stamp_over=(v == "stamp"))
            if v == "phone":
                (pages_dir / f"{pid}.jpg").write_bytes(jpeg(phone(base), 70))
                add(pid, key, v, "phone", spec)
            else:
                apply(v, base, seed).save(pages_dir / f"{pid}.png")
                add(pid, key, v, "scan", spec)
    for i, spec in enumerate(PHOTO_PAGES, start=1):
        pid = f"estimate-photo-{i}"
        (pages_dir / f"{pid}.jpg").write_bytes(jpeg(phone(render(spec)), 75))
        add(pid, "estimate_photos", "phone", "photos", spec)
    # 掃描後加上文字層的 PDF：圖是診斷證明書，文字層是不可靠的英文 OCR 結果
    seed += 1
    scan(render(DOCS["diagnosis"]), seed).save(pages_dir / "textlayer-scan.png")
    add("textlayer-scan", "diagnosis", "textpdf", "textpdf", DOCS["diagnosis"])
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


if __name__ == "__main__":
    t = generate(Path(sys.argv[1]))
    print(len(t), "pages")
