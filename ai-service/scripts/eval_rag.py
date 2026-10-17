"""RAG 召回率评测（黄金集 + 消融对比）

用法（在 ai-service 目录下执行）：
    python -m scripts.eval_rag --validate           # 只校验期望答案串是否存在于知识库
    python -m scripts.eval_rag                      # 跑全部模式并输出对比表
    python -m scripts.eval_rag --mode hybrid        # 只跑某一个模式

三种模式刻意对应「改造的三个阶段」，用于分离每一改动的贡献：

  A baseline-vector4  改造前线上行为：旧 langchain_pg_embedding 集合（11 条片段）
                      + 纯向量 top_k=4 + 余弦相似度阈值 0.35；
  B corpus-vector4    只换语料与切分，检索算法不变（新 kb_chunk 库 + 纯向量 top4 + 0.35）；
  D hybrid            最终方案：混合检索（向量 + BM25 关键词加权序值融合、物种降权、双门槛）。

判定标准不依赖人工标注片段 ID（切分一变 ID 就失效），改用「答案证据是否进候选」：
每条查询给出 expect 列表，**同一个片段内包含全部期望子串**即视为召回成功（hit）。
另外记录第一个命中片段的名次用于 MRR、以及各模式的空召回率。

指标：hit@1 / hit@3 / hit@5 / recall@5（=命中查询占比）/ MRR / 空召回率。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from app.config import settings  # noqa: E402
from app.kb import search, store  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

# 改造前的线上检索参数（不要跟着新配置改，这里就是要复现旧行为）
_LEGACY_TOP_K = 4
_LEGACY_THRESHOLD = 0.35

# 黄金集：expect 内的子串必须出现在同一个知识片段中
# 覆盖此前基线探针里系统性漏召的长尾问题（黑下巴、皮肤癣用药、老年犬关节等）
GOLDEN: List[Dict] = [
    {"q": "两个月的小猫什么时候打疫苗", "expect": ["幼猫", "8～9 周"]},
    {"q": "幼犬最后一针疫苗必须在多少周龄之后打", "expect": ["16 周龄"]},
    {"q": "打疫苗后多久可能过敏，需要在医院留观吗", "expect": ["留观 30 分钟"]},
    {"q": "抗体检测能确认疫苗有没有生效吗", "expect": ["titer"]},
    {"q": "狗狗皮肤癣用什么药", "expect": ["伊曲康唑", "特比萘芬"]},
    {"q": "猫癣会传染给人吗", "expect": ["人畜共患"]},
    {"q": "伍德氏灯照阴性能不能排除猫癣", "expect": ["阴性绝不能排除"]},
    {"q": "猫咪黑下巴怎么护理", "expect": ["黑下巴", "不锈钢"]},
    {"q": "猫耳朵里黑色咖啡渣一样的东西是什么", "expect": ["耳螨", "咖啡渣"]},
    {"q": "狗一直甩头抓耳朵还有异味", "expect": ["外耳炎"]},
    {"q": "公猫尿不出来多久必须去急诊", "expect": ["尿道阻塞", "72 小时"]},
    {"q": "猫频繁蹲猫砂盆每次只尿一点点", "expect": ["尿团"]},
    {"q": "猫喝水少会得什么病", "expect": ["泌尿"]},
    {"q": "老年猫检查肾病要看哪些指标", "expect": ["SDMA", "尿比重"]},
    {"q": "狗吃葡萄会中毒吗", "expect": ["葡萄", "急性肾"]},
    {"q": "巧克力中毒的剂量是多少", "expect": ["可可碱", "mg/kg"]},
    {"q": "木糖醇对狗有多危险", "expect": ["低血糖", "肝"]},
    {"q": "猫能吃泰诺吗", "expect": ["对乙酰氨基酚"]},
    {"q": "狗误食清洁剂能不能催吐", "expect": ["禁止催吐"]},
    {"q": "猫能用双氧水催吐吗", "expect": ["猫禁用"]},
    {"q": "夏天遛狗怎么判断路面烫不烫", "expect": ["5～7 秒"]},
    {"q": "狗中暑了正确的降温方法", "expect": ["冰水浸浴"]},
    {"q": "狗突然抽搐站不起来怎么办", "expect": ["不要往嘴里塞"]},
    {"q": "猫张口呼吸是不是急症", "expect": ["张口呼吸"]},
    {"q": "狗呕吐拉稀一天四五次精神很差", "expect": ["当天就医"]},
    {"q": "幼犬拉稀带血可能是细小吗", "expect": ["细小"]},
    {"q": "猫两天不吃东西会有生命危险吗", "expect": ["脂肪肝"]},
    {"q": "猫吐毛球其实是咳嗽吗", "expect": ["猫哮喘"]},
    {"q": "犬窝咳会持续多少天", "expect": ["10～20 天"]},
    {"q": "小型犬突然后腿跳几步是哪的问题", "expect": ["髌骨脱位", "跳几步"]},
    {"q": "拉布拉多突然三条腿走路膝盖肿", "expect": ["十字韧带"]},
    {"q": "达克斯猎犬后腿瘫了要注意什么", "expect": ["椎间盘"]},
    {"q": "狗关节不好能不能吃软骨素", "expect": ["并未显示明确的益处"]},
    {"q": "幼犬要不要额外补钙", "expect": ["不要自行给大型犬幼犬补钙"]},
    {"q": "犬猫一天喂几顿比较合适", "expect": ["幼犬", "每日 3"]},
    {"q": "怎么判断宠物胖瘦的体况评分", "expect": ["BCS", "肋骨"]},
    {"q": "宠物减重一周减多少安全", "expect": ["1%～2%"]},
    {"q": "猫能喝牛奶吗", "expect": ["乳糖"]},
    {"q": "换粮要几天过渡", "expect": ["7 天过渡"]},
    {"q": "猫一定要吃牛磺酸吗", "expect": ["牛磺酸", "视网膜"]},
    {"q": "无谷粮和心脏病有什么关系", "expect": ["DCM", "豌豆"]},
    {"q": "生骨肉喂养安全吗", "expect": ["AVMA"]},
    {"q": "狗口臭流口水牙齿黄", "expect": ["牙周"]},
    {"q": "猫口炎一定要拔牙吗", "expect": ["拔牙"]},
    {"q": "猫的牙吸收需要拍片吗", "expect": ["X 光"]},
    {"q": "能用人的牙膏给狗刷牙吗", "expect": ["人用牙膏"]},
    {"q": "洗牙为什么要全麻", "expect": ["全身麻醉"]},
    {"q": "猫多大适合绝育", "expect": ["4～6 月龄"]},
    {"q": "大型犬绝育要等到几岁", "expect": ["12～18 月龄"]},
    {"q": "母狗没绝育喝水特别多肚子变大", "expect": ["子宫蓄脓"]},
    {"q": "猫发情能不能吃激素药控制", "expect": ["孕激素"]},
    {"q": "狗怀孕多少天生", "expect": ["57～72 天"]},
    {"q": "母狗产后抽搐流口水", "expect": ["子痫"]},
    {"q": "孤儿奶猫多久喂一次", "expect": ["每 2～3 小时"]},
    {"q": "奶猫可以用牛奶喂吗", "expect": ["代乳粉"]},
    {"q": "小猫多大可以离开母猫领养", "expect": ["8 周"]},
    {"q": "幼犬社会化最佳时期", "expect": ["3～14 周"]},
    {"q": "打完疫苗能带出去遛吗", "expect": ["受控暴露"]},
    {"q": "狗一电击项圈就听话了行不行", "expect": ["都不应使用"]},
    {"q": "狗独处就拆家嚎叫流口水", "expect": ["分离焦虑"]},
    {"q": "猫乱尿在床上一开始好好的", "expect": ["医学排查"]},
    {"q": "家里养了两只猫要几个猫砂盆", "expect": ["猫数＋1"]},
    {"q": "狗坐车里把头伸窗外可以吗", "expect": ["伸出窗外"]},
    {"q": "法斗能飞机托运吗", "expect": ["短鼻"]},
    {"q": "带狗坐飞机需要什么证明", "expect": ["动物检疫合格证明"]},
    {"q": "过年鞭炮声狗害怕怎么办", "expect": ["脱敏"]},
    {"q": "猫需要从阳台防坠吗", "expect": ["封窗"]},
    {"q": "老年猫体检多久一次", "expect": ["每 6 个月"]},
    {"q": "老狗夜里一直叫来回走", "expect": ["认知"]},
    {"q": "猫甲亢的表现", "expect": ["吃得多", "消瘦"]},
    {"q": "犬猫正常体温是多少", "expect": ["38.0～39.2"]},
    {"q": "柯利犬能不能用伊维菌素", "expect": ["MDR1"]},
    {"q": "狗用了猫的驱虫滴剂会怎样", "expect": ["氯菊酯"]},
    {"q": "猫家里能点香薰精油吗", "expect": ["茶树"]},
    {"q": "狗洗澡多久一次合适", "expect": ["1～3 个月"]},
    {"q": "猫需要经常洗澡吗", "expect": ["自洁"]},
    {"q": "狗屁股有味还蹭地是怎么回事", "expect": ["肛门腺"]},
    {"q": "剪指甲剪出血了怎么办", "expect": ["止血粉"]},
    {"q": "狗每天要遛多久", "expect": ["运动量"]},
    {"q": "狗爆冲用项圈还是胸背带", "expect": ["胸背带"]},
    {"q": "疫苗打了还是得细小是什么原因", "expect": ["母源抗体"]},
    {"q": "体外驱虫多久一次科学", "expect": ["每月"]},
    {"q": "心丝虫要先检测吗", "expect": ["抗原检测"]},
]


async def _legacy_results(query: str) -> List[dict]:
    """模式 A：改造前的线上链路（langchain PGVector 旧集合 + top4 + 0.35 阈值）"""
    from langchain_postgres import PGVector
    from sqlalchemy import create_engine

    from app.llm import get_embeddings

    engine = create_engine(settings.pg_conn_str, pool_pre_ping=True)
    pg = PGVector(embeddings=get_embeddings(), connection=engine,
                  collection_name="petverse_kb", embedding_length=settings.EMBEDDING_DIM,
                  use_jsonb=True, async_mode=False, create_extension=False)
    pairs = await asyncio.to_thread(pg.similarity_search_with_score, query, _LEGACY_TOP_K)
    out = []
    for doc, distance in pairs:
        score = 1.0 - float(distance)
        if score < _LEGACY_THRESHOLD:
            continue
        out.append({"content": doc.page_content, "score": score})
    return out


async def _corpus_vector_results(query: str) -> List[dict]:
    """模式 B：新语料新切分，但沿用旧的纯向量 top4 + 0.35 单阈值"""
    from app.llm import get_embeddings

    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    rows = await store.dense_search(vector, _LEGACY_TOP_K)
    return [r for r in rows if r["sim"] >= _LEGACY_THRESHOLD]


async def _hybrid_results(query: str) -> List[dict]:
    """模式 D：最终混合检索链路"""
    return await search.retrieve(query)


_MODES = {
    "baseline": ("A 改造前（旧库·纯向量 top4·0.35）", _legacy_results),
    "corpus": ("B 只换语料（新库·纯向量 top4·0.35）", _corpus_vector_results),
    "hybrid": ("D 混合检索（向量+BM25·加权序值融合·双门槛）", _hybrid_results),
}


def _judge(results: Sequence[dict], expect: Sequence[str], depth: int = 5) -> Tuple[bool, int]:
    """判定：同一个片段内包含全部期望子串即算命中；返回 (是否命中, 首个命中的名次 0 表示未命中)"""
    for rank, item in enumerate(results[:depth], start=1):
        content = (item.get("content") or "").replace(" ", "")
        if all(e.replace(" ", "") in content for e in expect):
            return True, rank
    return False, 0


async def run_mode(name: str, golden: List[Dict], verbose: bool) -> dict:
    label, runner = _MODES[name]
    stats = {"label": label, "hit1": 0, "hit3": 0, "hit5": 0, "rr_sum": 0.0,
             "empty": 0, "misses": []}
    for case in golden:
        try:
            results = await runner(case["q"])
        except Exception as exc:
            print(f"  [跳过] {case['q']} 检索异常：{type(exc).__name__}: {exc}")
            results = []
        hit, rank = _judge(results, case["expect"])
        if not results:
            stats["empty"] += 1
        if hit:
            if rank <= 1:
                stats["hit1"] += 1
            if rank <= 3:
                stats["hit3"] += 1
            stats["hit5"] += 1
            stats["rr_sum"] += 1.0 / rank
        else:
            stats["misses"].append({"q": case["q"], "expect": case["expect"],
                                    "top": [r.get("content", "")[:70] for r in results[:2]]})
        if verbose:
            print(f"  {'HIT ' if hit else 'MISS'} rank={rank or '-'} {case['q']}")
    total = len(golden)
    stats["total"] = total
    stats["recall"] = stats["hit5"] / total if total else 0.0
    stats["mrr"] = stats["rr_sum"] / total if total else 0.0
    return stats


async def validate_expectations(golden: List[Dict]) -> int:
    """校验每条 expect 的子串能否在新知识库中找到（避免评测集自身写错）"""
    await store.init()
    try:
        joined = " ".join((r or "").replace(" ", "") for r in await store.all_contents())
        bad = []
        for case in golden:
            missing = [e for e in case["expect"] if e.replace(" ", "") not in joined]
            if missing:
                bad.append((case["q"], missing))
        if not bad:
            print(f"校验通过：{len(golden)} 条查询的期望答案子串都能在知识库中找到")
            return 0
        print(f"有 {len(bad)} 条查询的期望子串在知识库中找不到（请修正文档或评测集）：")
        for q, missing in bad:
            print(f"  - {q}  缺失：{missing}")
        return 1
    finally:
        await store.close()


async def main_async(args: argparse.Namespace) -> int:
    setup_logging()
    golden = GOLDEN
    if args.limit:
        golden = golden[:args.limit]

    if args.validate:
        return await validate_expectations(golden)

    modes = [args.mode] if args.mode else ["baseline", "corpus", "hybrid"]
    if not args.skip_legacy_check and "baseline" in modes:
        await _check_legacy_collection_exists()

    await store.init()
    try:
        report: Dict[str, dict] = {}
        for mode in modes:
            print(f"\n=== 模式 {_MODES[mode][0]} ===")
            report[mode] = await run_mode(mode, golden, verbose=args.verbose)

        print("\n" + "=" * 78)
        print(f"{'模式':<44}{'hit@1':>7}{'hit@3':>7}{'hit@5':>7}{'MRR':>8}{'空召回':>8}")
        print("-" * 78)
        for mode in modes:
            s = report[mode]
            total = s["total"]
            print(f"{s['label']:<44}{s['hit1'] / total:>7.1%}{s['hit3'] / total:>7.1%}"
                  f"{s['hit5'] / total:>7.1%}{s['mrr']:>8.3f}{s['empty'] / total:>8.1%}")
        print("=" * 78)
        print(f"评测集 {len(golden)} 条查询；语料规模：")
        stats = await store.stats()
        print(f"  新知识库 kb_chunk：文档 {stats['documents']} 篇 / 片段 {stats['chunks']} 条 "
              f"/ 正文 {stats['chars']} 字")

        if args.out:
            Path(args.out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"明细已写入 {args.out}")
        elif args.verbose:
            for mode in modes:
                for miss in report[mode]["misses"]:
                    print(f"[{mode}] MISS {miss['q']} 期望={miss['expect']}")
                    for t in miss["top"]:
                        print(f"        命中片段: {t}")
        return 0
    finally:
        await store.close()


async def _check_legacy_collection_exists() -> None:
    """模式 A 依赖改造前的 langchain 集合仍在库里（默认导入不会删除它）"""
    import psycopg

    def _count():
        with psycopg.connect(settings.pg_dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM langchain_pg_embedding")
                return cur.fetchone()[0]

    try:
        count = await asyncio.to_thread(_count)
    except Exception:
        print("[提示] 找不到 langchain_pg_embedding 旧集合，跳过 baseline 模式")
        return
    print(f"[baseline] 旧集合 langchain_pg_embedding 现存 {count} 条片段（改造前语料）")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 召回率评测")
    parser.add_argument("--mode", choices=list(_MODES), default=None, help="只跑指定模式")
    parser.add_argument("--validate", action="store_true", help="只校验评测集期望子串是否存在")
    parser.add_argument("--verbose", action="store_true", help="打印每条查询命中情况")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条查询")
    parser.add_argument("--out", type=str, default=None, help="把明细写成 JSON 文件")
    parser.add_argument("--skip-legacy-check", action="store_true")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
