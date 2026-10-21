package com.my.petverse.promotion.utils;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 基于回溯算法的全排列工具类：优惠券最优方案计算时，
 * 将所有可用券做全排列，枚举每一种"用券顺序"下的叠加优惠方案
 */
public final class PermuteUtil {

    private PermuteUtil() {
    }

    /**
     * 将 [0~n) 的所有数字重组，生成不重复的所有排列方案
     */
    public static List<List<Byte>> permute(int n) {
        List<List<Byte>> res = new ArrayList<>();
        List<Byte> input = new ArrayList<>(n);
        for (byte i = 0; i < n; i++) {
            input.add(i);
        }
        backtrack(n, input, res, 0);
        return res;
    }

    /**
     * 将指定集合中的元素重组，生成所有的排列组合方案
     */
    public static <T> List<List<T>> permute(List<T> input) {
        List<List<T>> res = new ArrayList<>();
        backtrack(input.size(), input, res, 0);
        return res;
    }

    private static <T> void backtrack(int n, List<T> input, List<List<T>> res, int first) {
        // 所有数都填完了
        if (first == n) {
            res.add(new ArrayList<>(input));
        }
        for (int i = first; i < n; i++) {
            // 动态维护数组
            Collections.swap(input, first, i);
            // 继续递归填下一个数
            backtrack(n, input, res, first + 1);
            // 撤销操作
            Collections.swap(input, first, i);
        }
    }
}
