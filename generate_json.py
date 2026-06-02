#!/usr/bin/env python3
"""
generate_json.py
oasis_統合_会社マスタ.csv から manufacturers.json を生成する。
HTML側は fetch('./manufacturers.json') で読み込む。
"""

import csv, json, re, os, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORK = str(SCRIPT_DIR)
OUTPUT_JSON = os.path.join(WORK, 'manufacturers.json')

# ============================================================
# ヘルパー
# ============================================================
def norm_oin(x):
    s = (x or '').strip()
    if s.endswith('.0'): s = s[:-2]
    return s

def to_float(s):
    try:
        v = float(s)
        if v == 0 and str(s).strip() in ('', '0', '0.0'):
            return None
        return v
    except (ValueError, TypeError):
        return None

def parse_furnace_size(text):
    if not text: return (None, None, None)
    t = text.replace('x','x').replace('X','x').replace(' ','').upper()
    t = t.replace('×','x').replace('Х','x').replace('✕','x')
    sizes = []
    pat1 = re.compile(r'[WF]?(\d+)X[HF]?(\d+)X[LDF]?(\d+)')
    pat2 = re.compile(r'[WF]?(\d+)X[HLDF]?(\d+)')
    for m in pat1.finditer(t):
        sizes.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if not sizes:
        for m in pat2.finditer(t):
            sizes.append((int(m.group(1)), int(m.group(2)), 0))
    if not sizes: return (None, None, None)
    best = max(sizes, key=lambda s: s[0]*max(1,s[1])*max(1,s[2]))
    return best

# ============================================================
# SHT工程名 -> 別名・同義語マッピング
# ============================================================
_SHT_SYNONYM_MAP = [
    ('化成処理',    ['アロジン','アロダイン','Alodine','クロメート','クロム酸処理','BONDERITE']),
    ('アロジン',    ['化成処理','アロダイン','Alodine','クロメート','クロム酸処理']),
    ('アロダイン',  ['化成処理','アロジン','Alodine','クロメート']),
    ('クロメート',  ['化成処理','アロジン','クロム酸処理']),
    ('アルマイト',  ['陽極酸化処理','アノダイズ','anodize','陽極酸化']),
    ('陽極酸化',   ['アルマイト','アノダイズ','anodize','陽極酸化処理']),
    ('アノダイズ',  ['アルマイト','陽極酸化処理','陽極酸化','anodize']),
    ('NDT',        ['非破壊検査','浸透探傷','磁粉探傷','FPI','MPI']),
    ('非破壊検査',  ['NDT','FPI','MPI','浸透探傷','磁粉探傷']),
    ('浸透探傷',   ['FPI','PT','NDT','非破壊検査','蛍光浸透探傷']),
    ('磁粉探傷',   ['MPI','MT','NDT','非破壊検査','磁粉探傷検査']),
    ('FPI',        ['浸透探傷','蛍光浸透探傷','NDT','非破壊検査']),
    ('MPI',        ['磁粉探傷','磁粉探傷検査','NDT','非破壊検査']),
    ('NADCAP',     ['ナドキャップ','nadcap']),
    ('ショットピーニング', ['shot peening','SP']),
    ('熱処理',     ['heat treatment']),
    ('窒化',       ['nitriding']),
    ('HIP',        ['熱間等方圧加圧']),
    ('パッシベート', ['不動態化処理','passivation']),
    ('不動態化処理', ['パッシベート','passivation']),
]

# nadcap_categoriesが「なし」「不明」系でないことを判定するパターン
_NADCAP_NONE_PATTERNS = [
    'なし', '不明', '未確認', '対象外', '情報なし', '該当なし', '確認不可',
]

def _is_real_nadcap(nadcap_str):
    """nadcap_categoriesが実際のNADCAPカテゴリを示すか判定。"""
    if not nadcap_str:
        return False
    return not any(p in nadcap_str for p in _NADCAP_NONE_PATTERNS)

def build_sht_search_text(process_types_str, nadcap_str=''):
    if not process_types_str:
        return ''
    parts = [process_types_str.replace(';', ' ')]
    # NADCAP情報：実際のカテゴリ名がある場合のみ「NADCAP ナドキャップ」を追加
    if _is_real_nadcap(nadcap_str):
        parts.append(nadcap_str)
        parts.append('NADCAP ナドキャップ')
    synonyms_added = set()
    combined = process_types_str + ' ' + (nadcap_str or '')
    for trigger, aliases in _SHT_SYNONYM_MAP:
        if trigger in combined:
            for alias in aliases:
                if alias not in combined and alias not in synonyms_added:
                    synonyms_added.add(alias)
    if synonyms_added:
        parts.append(' '.join(sorted(synonyms_added)))
    return ' '.join(p for p in parts if p)

def load_sht_oin_map(work_dir):
    """OINなし行は完全スキップ。ファジーマッチ禁止。"""
    sht_csv = os.path.join(work_dir, 'surface_heat_treatment_log.csv')
    if not os.path.exists(sht_csv):
        print('  [警告] surface_heat_treatment_log.csv なし。SHTスキップ。')
        return {}
    result = {}
    skipped_no_oin = 0
    with open(sht_csv, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            oin = norm_oin(row.get('oin', ''))
            if not oin:
                skipped_no_oin += 1
                continue
            if oin not in result:
                result[oin] = {
                    'process_types':     (row.get('process_types', '') or '').strip(),
                    'nadcap_categories': (row.get('nadcap_categories', '') or '').strip(),
                }
    print('  SHTログ: OINあり=%d社 / OINなしスキップ=%d行' % (len(result), skipped_no_oin))
    return result

# ============================================================
# 会社データ -> dict 変換
# ============================================================
def row_to_dict(r, NORM_DICT, LATEST_RESEARCH, emp_map, sht_oin_map=None):
    lat = to_float(r.get('Lat', ''))
    lng = to_float(r.get('Lon', ''))
    if lat is None or lng is None:
        return None

    raw_cat = (r.get('業種分類', '') or '').strip()
    if raw_cat in NORM_DICT:
        primary = NORM_DICT[raw_cat]['primary']
        secondaries = NORM_DICT[raw_cat].get('secondaries', []) or []
    else:
        primary, secondaries = 'other', []
    primary = 'other' if primary == 'unknown' else primary
    secondaries = [s for s in secondaries if s and s != 'unknown']
    cats = [primary] + secondaries

    oin = norm_oin(r.get('OIN_norm', ''))
    invest = (r.get('調査済み', '') or '').strip()
    is_oasis = (r.get('OASIS認証マッチ', '') or '').strip().upper() == 'YES'
    eq_cnt_s = (r.get('設備件数', '') or '').strip()
    dq = ('VERIFIED' if invest in ('済', 'OASIS統合')
          else ('BASIC' if eq_cnt_s not in ('', '0', '0.0') else ''))

    obj = {}
    obj['name'] = r.get('会社名', '') or ''
    obj['pref'] = r.get('都道府県', '') or ''
    obj['addr'] = r.get('住所_JP', '') or ''
    obj['lat']  = lat
    obj['lng']  = lng
    obj['cats'] = cats

    if raw_cat: obj['category'] = raw_cat
    if oin:     obj['oin']      = oin

    for col, key in [('認証規格','certStd'),('認証番号','certNo'),('認証機関','certOrg')]:
        v = (r.get(col,'') or '').strip()
        if v: obj[key] = v

    if eq_cnt_s and eq_cnt_s not in ('0','0.0'):
        try: obj['equipCount'] = int(float(eq_cnt_s))
        except: pass
    eq_type = (r.get('設備種別TOP3','') or '').strip()
    if eq_type: obj['equipText'] = eq_type

    # SHT工程テキストをequipTextに追記（OIN完全一致のみ、ファジーマッチ禁止）
    if sht_oin_map is not None and oin and oin in sht_oin_map:
        sht_data = sht_oin_map[oin]
        sht_search = build_sht_search_text(
            sht_data.get('process_types', ''),
            sht_data.get('nadcap_categories', ''),
        )
        if sht_search:
            existing = obj.get('equipText', '')
            obj['equipText'] = (existing + ' ' + sht_search).strip() if existing else sht_search

    for col, key in [('最大X_mm','maxX'),('最大Y_mm','maxY'),('最大Z_mm','maxZ')]:
        v = to_float(r.get(col,''))
        if v is not None and v > 0: obj[key] = v

    if oin and oin in LATEST_RESEARCH and 'sur' in LATEST_RESEARCH[oin]:
        fs_text = LATEST_RESEARCH[oin]['sur'].get('fs','')
        fx, fy, fz = parse_furnace_size(fs_text)
        if fx: obj['fX'] = fx
        if fy: obj['fY'] = fy
        if fz: obj['fZ'] = fz

    for col, key in [('主要メーカーTOP3','vendorText'),('対応材料TOP5','materialText'),('対応形状TOP5','shapeText')]:
        v = (r.get(col,'') or '').strip()
        if v: obj[key] = v

    for col, key in [('公式HP','hp'),('設備ページURL','ep')]:
        v = (r.get(col,'') or '').strip()
        if v: obj[key] = v

    if dq:       obj['dataQuality'] = dq
    if invest:   obj['_invest']     = invest
    if is_oasis: obj['_isOasis']    = True
    if dq == 'VERIFIED': obj['isWeb'] = True

    if oin in emp_map: obj['employees'] = emp_map[oin]

    note = (r.get('note','') or '').strip()
    if note: obj['note'] = note[:200]

    for col, key in [
        ('sht_process_types',       'shtProc'),
        ('sht_furnace_or_tank_size','shtFurnace'),
        ('sht_materials_ok',        'shtMatOk'),
        ('sht_materials_ng',        'shtMatNg'),
        ('sht_nadcap_categories',   'shtNadcap'),
        ('sht_certifications',      'shtCert'),
        ('sht_note',                'shtNote'),
    ]:
        v = (r.get(col,'') or '').strip()
        if v: obj[key] = v[:300]

    for col, key in [
        ('sht_boeing_approved','shtBoeing'),
        ('sht_airbus_approved','shtAirbus'),
        ('sht_nadcap_ht',      'shtNadcapHT'),
        ('sht_nadcap_cp',      'shtNadcapCP'),
        ('sht_nadcap_coatings','shtNadcapCoat'),
        ('sht_ams2750',        'shtAms2750'),
        ('sht_mil_spec',       'shtMilSpec'),
    ]:
        if (r.get(col,'') or '').strip() == 'True':
            obj[key] = True

    return obj

# ============================================================
# メイン: JSON 生成・書き出し
# ============================================================
def generate_json_output(master, NORM_DICT, LATEST_RESEARCH, emp_map, output_path=None, sht_oin_map=None):
    out_path = output_path or OUTPUT_JSON
    companies = []
    skipped = 0
    sht_enriched = 0
    for r in master:
        obj = row_to_dict(r, NORM_DICT, LATEST_RESEARCH, emp_map, sht_oin_map=sht_oin_map)
        if obj is None:
            skipped += 1
        else:
            companies.append(obj)
            if sht_oin_map is not None and obj.get('oin') and obj['oin'] in sht_oin_map:
                sht_enriched += 1
    if sht_oin_map is not None:
        print('  SHT工程テキスト追記: %d社' % sht_enriched)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(companies, f, ensure_ascii=False, separators=(',',':'))

    size_kb = os.path.getsize(out_path) / 1024
    print('  -> manufacturers.json: %d社 (skip座標なし: %d) / %.1f KB' % (len(companies), skipped, size_kb))
    return len(companies)


def main():
    print('[1/3] 入力ファイルロード...')
    with open(os.path.join(WORK, 'oasis_統合_会社マスタ.csv'), encoding='utf-8') as f:
        master = list(csv.DictReader(f))

    with open(os.path.join(WORK, 'category_normalization.json'), encoding='utf-8') as f:
        norm = json.load(f)
    NORM_DICT = norm['dictionary']

    with open(os.path.join(WORK, 'latest_research.json'), encoding='utf-8') as f:
        LATEST_RESEARCH = json.load(f)

    emp_map = {}
    emp_csv = os.path.join(WORK, 'company_info_with_oin.csv')
    if os.path.exists(emp_csv):
        with open(emp_csv, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                o = norm_oin(row.get('oin',''))
                emp = (row.get('従業員数_テキスト','') or '').strip()
                if o and emp and emp != '不明':
                    emp_map[o] = emp[:50]
    print('  master: %d, emp_map: %d, LATEST_RESEARCH: %d' % (len(master), len(emp_map), len(LATEST_RESEARCH)))

    print('[1.5/3] SHTログ読み込み(OIN完全一致のみ)...')
    sht_oin_map = load_sht_oin_map(WORK)

    print('[2/3] JSON生成...')
    count = generate_json_output(master, NORM_DICT, LATEST_RESEARCH, emp_map, sht_oin_map=sht_oin_map)

    print('[3/3] JSON構文確認...')
    with open(OUTPUT_JSON, encoding='utf-8') as f:
        data = json.load(f)
    print('  JSON valid OK (%d件)' % len(data))
    print('  出力先: %s' % OUTPUT_JSON)


if __name__ == '__main__':
    main()
