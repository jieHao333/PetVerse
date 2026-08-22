package com.my.petverse.common.util;

import com.my.petverse.common.entity.pet.Pet;

/**
 * 宠物等级经验计算工具类
 * 等级上限 100，升级所需经验公式：100 + (level-1) * 50
 */
public final class PetLevelCalculator {

    /** 宠物最高等级 */
    public static final int MAX_LEVEL = 100;

    /** 1 级升 2 级所需基础经验 */
    public static final long BASE_EXP = 100L;

    /** 每提升一级经验需求的增量 */
    public static final long EXP_INCREMENT_PER_LEVEL = 50L;

    private PetLevelCalculator() {
    }

    /**
     * 计算从当前等级升到下一级所需的经验值
     *
     * @param level 当前等级
     * @return 所需经验值，满级返回 0
     */
    public static long expNeededForNextLevel(int level) {
        if (level >= MAX_LEVEL) {
            return 0;
        }
        return BASE_EXP + (long) (level - 1) * EXP_INCREMENT_PER_LEVEL;
    }

    /**
     * 给宠物增加经验值并处理升级，经验溢出自动结转至下一级
     * 满级后多余经验不再累积，exp 清零
     *
     * @param pet       宠物实体，会就地修改 level 和 exp
     * @param gainedExp 本次获得的经验值
     * @return 本次提升的等级数
     */
    public static int gainExp(Pet pet, long gainedExp) {
        if (pet == null || gainedExp <= 0) {
            return 0;
        }
        int level = pet.getLevel() == null ? 1 : pet.getLevel();
        long exp = pet.getExp() == null ? 0L : pet.getExp();
        int levelBefore = level;
        long total = exp + gainedExp;
        // 满足升级条件则升级并结转溢出经验，直到满级
        while (level < MAX_LEVEL) {
            long needed = expNeededForNextLevel(level);
            if (total < needed) {
                break;
            }
            total -= needed;
            level++;
        }
        pet.setLevel(level);
        pet.setExp(level >= MAX_LEVEL ? 0L : total);
        return level - levelBefore;
    }
}
