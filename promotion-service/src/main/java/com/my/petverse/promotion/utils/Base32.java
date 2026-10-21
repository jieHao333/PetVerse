package com.my.petverse.promotion.utils;

/**
 * 将整数转为 Base32 字符的工具，因为是 32 进制，所以每 5 个 bit 位转一次。
 * 码表为自定义乱序表，避免生成可被预测的顺序兑换码
 */
public class Base32 {

    private static final String BASE_CHARS = "6CSB7H8DAKXZF3N95RTMVUQG2YE4JWPL";

    private Base32() {
    }

    /** 整数编码为 Base32 字符串 */
    public static String encode(long raw) {
        StringBuilder sb = new StringBuilder();
        while (raw != 0) {
            int i = (int) (raw & 0b11111);
            sb.append(BASE_CHARS.charAt(i));
            raw = raw >>> 5;
        }
        return sb.toString();
    }

    /** Base32 字符串解码为整数 */
    public static long decode(String code) {
        long r = 0;
        char[] chars = code.toCharArray();
        for (int i = chars.length - 1; i >= 0; i--) {
            long n = BASE_CHARS.indexOf(chars[i]);
            r = r | (n << (5 * i));
        }
        return r;
    }
}
