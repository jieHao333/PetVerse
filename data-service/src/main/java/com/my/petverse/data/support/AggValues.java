package com.my.petverse.data.support;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Date;
import java.util.Map;

/**
 * 聚合结果数值安全转换工具。SQL 聚合返回的 SUM/COUNT 可能是 BigDecimal/Long，
 * MAX(时间) 可能是 LocalDateTime/Timestamp，统一在此归一，避免类型转换异常。
 */
public final class AggValues {

    private AggValues() {
    }

    /** 转 long，null/非法返回 0 */
    public static long asLong(Object v) {
        if (v instanceof Number n) {
            return n.longValue();
        }
        if (v == null) {
            return 0L;
        }
        try {
            return Long.parseLong(v.toString());
        } catch (NumberFormatException e) {
            return 0L;
        }
    }

    /** 转 double，null/非法返回 0 */
    public static double asDouble(Object v) {
        if (v instanceof Number n) {
            return n.doubleValue();
        }
        if (v == null) {
            return 0d;
        }
        try {
            return Double.parseDouble(v.toString());
        } catch (NumberFormatException e) {
            return 0d;
        }
    }

    /** 转 LocalDateTime，无法识别返回 null */
    public static LocalDateTime asDateTime(Object v) {
        if (v instanceof LocalDateTime t) {
            return t;
        }
        if (v instanceof java.sql.Timestamp ts) {
            return ts.toLocalDateTime();
        }
        if (v instanceof Date d) {
            return LocalDateTime.ofInstant(d.toInstant(), ZoneId.systemDefault());
        }
        return null;
    }

    /** 从聚合行取 long */
    public static long getLong(Map<String, Object> row, String key) {
        return asLong(row.get(key));
    }

    /** 从聚合行取 double */
    public static double getDouble(Map<String, Object> row, String key) {
        return asDouble(row.get(key));
    }
}