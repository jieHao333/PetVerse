package com.my.petverse.promotion.utils;

import org.springframework.beans.BeanUtils;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Collection;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 促销服务通用工具：集合判空、数字转换、金额格式化、属性拷贝
 */
public final class PromotionUtils {

    private PromotionUtils() {
    }

    /** 集合是否为空 */
    public static boolean isEmpty(Collection<?> coll) {
        return coll == null || coll.isEmpty();
    }

    /** 空集合（不可变） */
    public static <T> List<T> emptyList() {
        return List.of();
    }

    /** 两个集合的交集（依赖元素 equals，默认对象即引用相等） */
    public static <T> List<T> intersection(Collection<T> a, Collection<T> b) {
        if (isEmpty(a) || isEmpty(b)) {
            return emptyList();
        }
        return a.stream().filter(b::contains).collect(Collectors.toList());
    }

    /** Long 转 int，null 按 0 处理 */
    public static int null2Zero(Long value) {
        return value == null ? 0 : value.intValue();
    }

    /** 字符串转 long，无法解析返回 0 */
    public static long parseLong(String value) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /**
     * 使用 {} 占位符格式化字符串（按顺序替换）
     */
    public static String format(String template, Object... params) {
        if (template == null || params == null || params.length == 0) {
            return template;
        }
        StringBuilder sb = new StringBuilder(template.length() + 32);
        int paramIndex = 0;
        int start = 0;
        int brace;
        while (paramIndex < params.length && (brace = template.indexOf("{}", start)) >= 0) {
            sb.append(template, start, brace).append(params[paramIndex++]);
            start = brace + 2;
        }
        sb.append(template.substring(start));
        return sb.toString();
    }

    /**
     * 分值金额转字符串：如 scaleToStr(500, 2) → "5.00"（分转元），
     * scaleToStr(80, 1) → "8.0"（折扣率）
     */
    public static String scaleToStr(int value, int scale) {
        long divisor = 1;
        for (int i = 0; i < scale; i++) {
            divisor *= 10;
        }
        long integer = value / divisor;
        long fraction = Math.abs(value) % divisor;
        return String.format("%d.%0" + scale + "d", integer, fraction);
    }

    /** LocalDateTime 转秒级时间戳（与 Lua 脚本 redis.call('time') 的秒级返回值同单位比较） */
    public static long toEpochSecond(LocalDateTime time) {
        return time.atZone(ZoneId.systemDefault()).toInstant().getEpochSecond();
    }

    /** 属性拷贝（同名同类型属性） */
    public static <T> T copyBean(Object source, Class<T> targetClass) {
        if (source == null) {
            return null;
        }
        T target;
        try {
            target = targetClass.getDeclaredConstructor().newInstance();
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("实例化失败: " + targetClass.getName(), e);
        }
        BeanUtils.copyProperties(source, target);
        return target;
    }

    /** 集合属性拷贝 */
    public static <S, T> List<T> copyList(List<S> source, Class<T> targetClass) {
        if (isEmpty(source)) {
            return emptyList();
        }
        return source.stream().map(s -> copyBean(s, targetClass)).collect(Collectors.toList());
    }
}
