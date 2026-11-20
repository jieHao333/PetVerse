"""PetVerse 养宠实用计算 MCP 服务（stdio）。

为 AI 养宠顾问补充内置工具没有的「确定性养护计算」能力：宠物年龄换算、每日喂食量
（RER/MER 兽医公式）、每日饮水量、常见有毒食物风险初筛。全部为只读计算，无副作用、
无网络依赖、不读取用户数据，可安全地交给模型按需调用。

运行（需安装 mcp 包，即 requirements.txt 里的 langchain-mcp-adapters 会带入）：
    python mcp_servers/petcare_tools.py

在「AI 扩展管理」页注册：传输选 Stdio，命令填装了 mcp 的 Python 解释器绝对路径，
参数填本脚本绝对路径（每行一个）。注册启用后，对话 Agent 即可调用下列工具。

计算口径均为常见兽医参考公式，结果仅作养护参考，不能替代兽医诊断。
"""

from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("petverse-petcare-tools")

Species = Annotated[Literal["猫", "狗"], Field(description="宠物物种：猫 或 狗")]

_DISCLAIMER = "本结果为常见兽医参考公式的估算，仅供养护参考，不能替代兽医诊断；如有异常请及时就医。"


def _norm_species(species: str) -> str:
    s = (species or "").strip().lower()
    if s in ("猫", "cat", "feline"):
        return "猫"
    if s in ("狗", "犬", "dog", "canine"):
        return "狗"
    raise ValueError("species 仅支持「猫」或「狗」")


# ---------------- 年龄换算 ----------------

_DOG_SIZE_STEP = {"小型": 4, "中型": 5, "大型": 6, "巨型": 7}


def _age_to_human(species: str, age_years: float, dog_size: str) -> dict:
    if age_years < 0:
        raise ValueError("age_years 不能为负")
    if species == "猫":
        # 猫：第 1 年≈15 岁，第 2 年≈+9（2 岁≈24），之后每年 +4。
        if age_years <= 1:
            human = age_years * 15
        elif age_years <= 2:
            human = 15 + (age_years - 1) * 9
        else:
            human = 24 + (age_years - 2) * 4
        rule = "猫：1 岁≈人类 15 岁，2 岁≈24 岁，之后每增 1 岁≈+4 岁"
    else:
        step = _DOG_SIZE_STEP.get(dog_size, 5)
        if age_years <= 1:
            human = age_years * 15
        elif age_years <= 2:
            human = 15 + (age_years - 1) * 9
        else:
            human = 24 + (age_years - 2) * step
        rule = f"狗：1 岁≈人类 15 岁，2 岁≈24 岁，之后{dog_size}犬每增 1 岁≈+{step} 岁"
    return {
        "species": species,
        "ageYears": age_years,
        "humanEquivalentYears": round(human, 1),
        "rule": rule,
        "summary": f"{age_years} 岁的{species}约相当于人类 {round(human, 1)} 岁。",
    }


@mcp.tool()
def pet_age_to_human(
    species: Species,
    age_years: Annotated[float, Field(description="宠物实际年龄（岁），可为小数，如 0.5 表示半岁", ge=0)],
    dog_size: Annotated[Literal["小型", "中型", "大型", "巨型"], Field(description="狗的体型分类（仅狗需要，猫忽略）")] = "中型",
) -> dict:
    """把猫 / 狗的实际年龄换算成人类等效年龄，用于理解宠物所处生命阶段。

    采用常见兽医参考曲线：第 1 年≈人类 15 岁、第 2 年≈24 岁，之后猫每年 +4、
    狗按体型每年 +4~7。返回人类等效年龄与换算口径。
    """
    return _age_to_human(_norm_species(species), age_years, dog_size)


# ---------------- 每日喂食量（RER / MER） ----------------

# 维持能量需求系数（MER = RER × 系数），按物种 / 生命阶段 / 是否绝育取值。
_MER_FACTORS = {
    "狗": {"幼年": 2.0, "成年": {True: 1.6, False: 1.8}, "老年": 1.4},
    "猫": {"幼年": 2.5, "成年": {True: 1.2, False: 1.4}, "老年": 1.1},
}


def _feeding(species: str, weight_kg: float, life_stage: str, neutered: bool,
             food_kcal_per_g: float) -> dict:
    if weight_kg <= 0:
        raise ValueError("weight_kg 必须大于 0")
    if food_kcal_per_g <= 0:
        raise ValueError("food_kcal_per_g 必须大于 0")
    stage = _MER_FACTORS[species]
    if life_stage not in stage:
        raise ValueError("life_stage 仅支持「幼年 / 成年 / 老年」")
    factor = stage[life_stage]
    if isinstance(factor, dict):
        factor = factor[bool(neutered)]
    rer = 70 * (weight_kg ** 0.75)
    mer = rer * factor
    grams = mer / food_kcal_per_g
    return {
        "species": species,
        "weightKg": weight_kg,
        "lifeStage": life_stage,
        "neutered": bool(neutered),
        "rerKcal": round(rer, 1),
        "merFactor": factor,
        "dailyKcal": round(mer, 1),
        "foodKcalPerG": food_kcal_per_g,
        "referenceGrams": round(grams, 1),
        "summary": (f"{weight_kg}kg {life_stage}{species}（{'已' if neutered else '未'}绝育）"
                    f"每日约需 {round(mer, 1)} kcal；按 {food_kcal_per_g} kcal/g 的粮折算约 {round(grams, 1)} 克/天。"),
        "note": "RER=70×体重(kg)^0.75，MER=RER×系数；实际喂量需结合体况评分与体重变化调整。",
        "disclaimer": _DISCLAIMER,
    }


@mcp.tool()
def daily_feeding_amount(
    species: Species,
    weight_kg: Annotated[float, Field(description="宠物体重（千克）", gt=0)],
    life_stage: Annotated[Literal["幼年", "成年", "老年"], Field(description="生命阶段")],
    neutered: Annotated[bool, Field(description="是否已绝育（影响能量需求系数）")] = True,
    food_kcal_per_g: Annotated[float, Field(description="所用宠粮的能量密度（kcal/克），干粮常见约 3.5，包装代谢能换算而来", gt=0)] = 3.5,
) -> dict:
    """按兽医 RER/MER 公式估算宠物每日所需能量（kcal）与参考喂食克数。

    RER（静息能量）=70×体重^0.75，再乘生命阶段/绝育系数得 MER（维持能量）。
    用于回答「我家猫/狗一天该喂多少」这类问题；结果是参考估算，需结合体况调整。
    """
    return _feeding(_norm_species(species), weight_kg, life_stage, neutered, food_kcal_per_g)


# ---------------- 每日饮水量 ----------------


def _water(species: str, weight_kg: float) -> dict:
    if weight_kg <= 0:
        raise ValueError("weight_kg 必须大于 0")
    low, high = 50 * weight_kg, 60 * weight_kg
    return {
        "species": species,
        "weightKg": weight_kg,
        "dailyWaterMlLow": round(low, 1),
        "dailyWaterMlHigh": round(high, 1),
        "summary": f"{weight_kg}kg 的{species}每日参考饮水量约 {round(low)}~{round(high)} 毫升。",
        "note": "按 50~60 ml/kg/天 估算；吃湿粮会减少饮水、运动/高温/泌乳会增加，肾病等需遵医嘱。",
        "disclaimer": _DISCLAIMER,
    }


@mcp.tool()
def daily_water_intake(
    species: Species,
    weight_kg: Annotated[float, Field(description="宠物体重（千克）", gt=0)],
) -> dict:
    """估算宠物每日参考饮水量（毫升），按 50~60 ml/kg/天 的常见维持需求给出区间。"""
    return _water(_norm_species(species), weight_kg)


# ---------------- 有毒食物风险初筛 ----------------

# 巧克力按类型的可可碱含量（mg/g，取常见中值）。
_CHOCOLATE_MG_PER_G = {"白巧克力": 0.25, "牛奶巧克力": 2.0, "黑巧克力": 8.0, "可可粉": 20.0}


def _chocolate_risk(weight_kg: float, amount_g: float, choc_type: str) -> dict:
    mg_per_g = _CHOCOLATE_MG_PER_G.get(choc_type, 8.0)
    dose = amount_g * mg_per_g / weight_kg  # 可可碱 mg/kg
    if dose < 20:
        level, detail = "低", "低于常见中毒阈值（约 20 mg/kg），仍建议观察"
    elif dose < 40:
        level, detail = "中", "达到轻度中毒区间（约 20~40 mg/kg），可能呕吐腹泻"
    elif dose < 60:
        level, detail = "高", "达到中度中毒区间（约 40~60 mg/kg），可能心律异常"
    else:
        level, detail = "紧急", "超过 60 mg/kg，有抽搐甚至危及生命风险，立即就医"
    return {
        "food": f"巧克力（{choc_type}）", "toxin": "可可碱",
        "estimatedDose": f"{round(dose, 1)} mg/kg", "riskLevel": level,
        "detail": detail,
        "advice": "尽快联系宠物医院，告知种类与摄入量；不要自行催吐除非兽医指导。",
    }


def _toxic_risk(species: str, food: str, weight_kg: float, amount_g: Optional[float],
                choc_type: str = "黑巧克力") -> dict:
    if weight_kg <= 0:
        raise ValueError("weight_kg 必须大于 0")
    f = (food or "").strip().lower()
    amt = amount_g if amount_g is not None else 0.0

    def wrap(base: dict) -> dict:
        base.update({"species": species, "weightKg": weight_kg,
                     "amountG": amount_g, "disclaimer": _DISCLAIMER})
        base["summary"] = f"{species}摄入{base['food']}：风险等级【{base['riskLevel']}】。{base['advice']}"
        return base

    if any(k in f for k in ("巧克力", "chocolate", "可可", "cocoa")):
        # 类型优先用调用方显式给出的 choc_type；非法值回落黑巧克力（按最不利情形估算）。
        choc_type = choc_type if choc_type in _CHOCOLATE_MG_PER_G else "黑巧克力"
        if amount_g is None:
            return wrap({"food": f"巧克力（{choc_type}）", "toxin": "可可碱", "riskLevel": "中",
                         "detail": "未提供摄入量，按可能中毒处理",
                         "advice": "巧克力对猫狗有毒，请尽快联系宠物医院并告知摄入量。"})
        return wrap(_chocolate_risk(weight_kg, amt, choc_type))

    if any(k in f for k in ("葡萄", "葡萄干", "grape", "raisin")):
        return wrap({"food": "葡萄 / 葡萄干", "toxin": "机制不明（可致急性肾衰）", "riskLevel": "紧急",
                     "detail": "个体差异大，任意剂量都可能中毒，无安全阈值",
                     "advice": "无论吃了多少都应立即就医，尽早催吐/活性炭处理（由兽医操作）。"})

    if any(k in f for k in ("木糖醇", "xylitol")):
        if species == "猫":
            return wrap({"food": "木糖醇", "toxin": "木糖醇", "riskLevel": "中",
                         "detail": "猫敏感性低于狗，但仍需警惕", "advice": "联系兽医评估摄入量。"})
        dose = amt / weight_kg if amount_g is not None else None
        if dose is None:
            level, detail = "高", "未提供摄入量；狗对木糖醇极敏感"
        elif dose >= 0.5:
            level, detail = "紧急", f"≥0.5 g/kg（{round(dose, 2)} g/kg），有肝衰竭风险"
        elif dose >= 0.1:
            level, detail = "高", f"≥0.1 g/kg（{round(dose, 2)} g/kg），可致严重低血糖"
        else:
            level, detail = "中", "低于常见中毒阈值，仍建议观察并咨询兽医"
        return wrap({"food": "木糖醇", "toxin": "木糖醇", "estimatedDose": f"{round(dose, 2)} g/kg" if dose else None,
                     "riskLevel": level, "detail": detail, "advice": "狗误食木糖醇需立即就医，监测血糖与肝功能。"})

    if any(k in f for k in ("洋葱", "大蒜", "葱", "onion", "garlic", "韭菜")):
        factor = 3 if ("蒜" in f or "garlic" in f) else 1
        dose = amt * factor / weight_kg if amount_g is not None else None
        if dose is None:
            level, detail = "中", "未提供摄入量；葱属植物可致溶血性贫血"
        elif dose >= 5:
            level, detail = "高", f"≥5 g/kg（折算 {round(dose, 1)} g/kg），溶血风险高"
        elif dose >= 1:
            level, detail = "中", "达到需警惕区间，可能损伤红细胞"
        else:
            level, detail = "低", "低于常见中毒阈值，仍建议观察"
        return wrap({"food": "葱属植物（洋葱/大蒜等）", "toxin": "有机硫化物（致溶血）",
                     "estimatedDose": f"{round(dose, 1)} g/kg" if dose else None,
                     "riskLevel": level, "detail": detail, "advice": "避免喂食；已摄入请联系兽医，观察牙龈苍白/乏力等贫血征象。"})

    if any(k in f for k in ("酒精", "酒", "alcohol", "咖啡", "caffeine", "茶")):
        return wrap({"food": food, "toxin": "乙醇 / 咖啡因等", "riskLevel": "高",
                     "detail": "猫狗代谢能力差，少量即可中毒", "advice": "立即就医，告知种类与摄入量。"})

    if any(k in f for k in ("夏威夷果", "macadamia")):
        dose = amt / weight_kg if amount_g is not None else None
        level = "高" if (dose is None or dose >= 2) else "中"
        return wrap({"food": "夏威夷果", "toxin": "机制不明（致虚弱/震颤）", "riskLevel": level,
                     "detail": "狗约 2 g/kg 即可出现症状", "advice": "停止喂食并联系兽医，多数预后良好但需评估。"})

    return wrap({"food": food, "toxin": "未收录", "riskLevel": "中",
                 "detail": "该食物不在本工具常见毒物清单内，无法判定安全性",
                 "advice": "不确定是否有毒时，请按可能有害处理并咨询兽医，不要继续喂食。"})


@mcp.tool()
def toxic_food_risk(
    species: Species,
    food: Annotated[str, Field(description="误食的食物名称，如 黑巧克力 / 葡萄 / 洋葱 / 木糖醇")],
    weight_kg: Annotated[float, Field(description="宠物体重（千克）", gt=0)],
    amount_g: Annotated[Optional[float], Field(description="估计摄入量（克），未知可省略；提供后可按剂量细化风险", ge=0)] = None,
    chocolate_type: Annotated[Literal["白巧克力", "牛奶巧克力", "黑巧克力", "可可粉"], Field(description="巧克力类型（仅食物为巧克力时影响结果）")] = "黑巧克力",
) -> dict:
    """对猫狗常见有毒食物做风险初筛（巧克力/葡萄/木糖醇/葱属/酒精咖啡因/夏威夷果等）。

    结合体重与摄入量估算剂量并给出风险等级（低/中/高/紧急）与就医建议。
    这是初步参考而非诊断：疑似中毒一律建议尽快联系宠物医院。未收录的食物按「可能有害」处理。
    """
    return _toxic_risk(_norm_species(species), food, weight_kg, amount_g, chocolate_type)


if __name__ == "__main__":
    mcp.run()