"""RAG 召回率评测脚本（消融对比）。

职责：用一份「黄金查询集」（问题 + 答案证据子串）批量跑不同检索方案，统计 hit@1 / hit@3 /
hit@5 / MRR / 空召回率，用于回答「这套检索到底比改造前好多少、好在哪一环」。

主要导出：无（作为脚本执行）。

三种评测模式（对应改造的三个阶段，差异只在检索实现）：
    - ``baseline``：改造前——旧集合（LangChain PGVector）+ 纯向量 top4 + 0.35 阈值
    - ``corpus``：  只换语料与切分——新库（kb_chunk）+ 同样的纯向量 top4 + 0.35 阈值
    - ``hybrid``：  最终方案——向量 + BM25 混合检索、物种过滤、双门槛

外部依赖：``app.kb.search`` / ``app.kb.store``、``app.llm.get_embeddings``，
``baseline`` 模式额外需要 ``langchain_postgres`` / ``sqlalchemy``（仅评测使用）。

使用示例：
    python -m scripts.eval_rag --validate      # 先校验期望子串确实存在于知识库
    python -m scripts.eval_rag                 # 跑全部三种模式
    python -m scripts.eval_rag --mode hybrid --verbose
    python -m scripts.eval_rag --out report.json

判定口径：只要「期望证据子串」全部出现在某个命中片段的正文里，即视为该条命中；
记录它出现的排名用于算 hit@k 与 MRR。这个口径衡量的是「证据是否进了候选」，
而非「最终答案是否正确」——后者受生成质量影响，不适合用来评价检索。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# 使脚本可直接执行时也能 import app 包。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from app.config import settings  # noqa: E402
from app.kb import search, store  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

# 改造前的检索参数，用于复现 baseline / corpus 两个对照组的原始口径。
_LEGACY_TOP_K = 4
_LEGACY_THRESHOLD = 0.35

# 黄金查询集：覆盖疫苗、皮肤病、泌尿、中毒、急症、营养、口腔、绝育、行为、出行、
# 老年照护、寄生虫、日常护理等语料主题。每个 ``expect`` 里的子串都必须能在知识库中
# 找到（用 --validate 校验），否则该条永远不可能命中，会虚低整体分数。
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
    """baseline 模式：查改造前的旧向量集合。

    Args:
        query: 查询语句。

    Returns:
        命中片段列表（仅低于阈值的被过滤掉）。

    Raises:
        Exception: 旧集合不存在或依赖未安装时抛出，由调用方跳过该条。
    """
    from langchain_postgres import PGVector
    from sqlalchemy import create_engine

    from app.llm import get_embeddings

    # 每次查询都新建引擎：评测是一次性脚本，不必优化连接复用；
    # 用 engine 而非连接串是为了适配 PGVector 的同步接口。
    engine = create_engine(settings.pg_conn_str, pool_pre_ping=True)
    pg = PGVector(embeddings=get_embeddings(), connection=engine,
                  collection_name="petverse_kb", embedding_length=settings.EMBEDDING_DIM,
                  use_jsonb=True, async_mode=False, create_extension=False)
    pairs = await asyncio.to_thread(pg.similarity_search_with_score, query, _LEGACY_TOP_K)
    out = []
    for doc, distance in pairs:
        # 旧实现返回的是余弦距离，这里换算成相似度以保持与其它模式可比。
        score = 1.0 - float(distance)
        if score < _LEGACY_THRESHOLD:
            continue
        out.append({"content": doc.page_content, "score": score})
    return out


async def _corpus_vector_results(query: str) -> List[dict]:
    """corpus 模式：新库 + 纯向量检索（不加关键词通道）。

    作用是把「换语料」与「换算法」两个变量的影响拆开——这正是消融实验的核心。

    Args:
        query: 查询语句。

    Returns:
        过阈值后的片段列表。

    Raises:
        Exception: 向量化或查询失败时抛出，由调用方跳过该条。
    """
    from app.llm import get_embeddings

    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    rows = await store.dense_search(vector, _LEGACY_TOP_K)
    return [r for r in rows if r["sim"] >= _LEGACY_THRESHOLD]


async def _hybrid_results(query: str) -> List[dict]:
    """hybrid 模式：走线上真实的混合检索链路。

    Args:
        query: 查询语句。

    Returns:
        检索结果列表。

    Raises:
        Exception: 检索链路异常时抛出，由调用方跳过该条。
    """
    return await search.retrieve(query)


_MODES = {
    "baseline": ("A 改造前（旧库·纯向量 top4·0.35）", _legacy_results),
    "corpus": ("B 只换语料（新库·纯向量 top4·0.35）", _corpus_vector_results),
    "hybrid": ("D 混合检索（向量+BM25·加权序值融合·双门槛）", _hybrid_results),
}


def _judge(results: Sequence[dict], expect: Sequence[str], depth: int = 5) -> Tuple[bool, int]:
    """判断某条查询是否命中，并返回命中排名。

    Args:
        results: 检索结果（按相关度降序）。
        expect: 期望出现的证据子串（必须全部出现才算命中）。
        depth: 只在前 depth 条里找。

    Returns:
        ``(是否命中, 排名)``；未命中时排名为 0。

    Raises:
        无。
    """
    for rank, item in enumerate(results[:depth], start=1):
        # 去掉空格再比较：知识库文本里的数字/单位常带空格（"72 小时"），
        # 而模型与用户写法不一定一致，去掉空格能减少无意义的漏判。
        content = (item.get("content") or "").replace(" ", "")
        if all(e.replace(" ", "") in content for e in expect):
            return True, rank
    return False, 0


async def run_mode(name: str, golden: List[Dict], verbose: bool) -> dict:
    """跑完一个模式的全部查询并汇总指标。

    Args:
        name: 模式名（``_MODES`` 的键）。
        golden: 查询集。
        verbose: 是否逐条打印命中情况。

    Returns:
        ``{label, hit1, hit3, hit5, rr_sum, empty, misses, total, recall, mrr}``。

    Raises:
        无——单条查询异常按「未命中」计入，避免个别坏样本中断整轮评测。
    """
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
            # 空召回单独计数：它与「召回了但不相关」是两类问题，排查方向完全不同。
            stats["empty"] += 1
        if hit:
            # 一次判定即可同时得出 hit@1/@3/@5：命中排名落在哪个区间就累加哪个计数。
            if rank <= 1:
                stats["hit1"] += 1
            if rank <= 3:
                stats["hit3"] += 1
            stats["hit5"] += 1
            # MRR 用排名倒数累加，越靠前贡献越大。
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
    """校验期望子串确实存在于知识库中。

    这一步很容易被跳过、却很重要：写错一个字的期望子串会让该条永远 MISS，
    从而把整体召回率算低，误导后续所有优化判断。

    Args:
        golden: 查询集。

    Returns:
        0 表示全部通过，1 表示存在缺失（供 CI 判断）。

    Raises:
        无。
    """
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
    """评测主流程。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码（0 正常完成）。

    Raises:
        无。
    """
    setup_logging()
    golden = GOLDEN
    if args.limit:
        golden = golden[:args.limit]

    if args.validate:
        return await validate_expectations(golden)

    modes = [args.mode] if args.mode else ["baseline", "corpus", "hybrid"]
    if not args.skip_legacy_check and "baseline" in modes:
        # 旧集合不存在时提前提示，否则每条 baseline 查询都会抛异常、刷屏。
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
            # 未指定输出文件时，把漏召回明细直接打到终端，方便当场看「为什么没命中」。
            for mode in modes:
                for miss in report[mode]["misses"]:
                    print(f"[{mode}] MISS {miss['q']} 期望={miss['expect']}")
                    for t in miss["top"]:
                        print(f"        命中片段: {t}")
        return 0
    finally:
        await store.close()


async def _check_legacy_collection_exists() -> None:
    """检查旧向量集合是否存在（不存在则 baseline 无意义）。

    Returns:
        None。

    Raises:
        无——表不存在只打印提示，不中断评测。
    """
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
    """命令行入口。

    Returns:
        进程退出码。

    Raises:
        SystemExit: 参数非法时由 argparse 抛出。
    """
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
