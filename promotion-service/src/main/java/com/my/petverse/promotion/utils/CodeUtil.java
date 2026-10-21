package com.my.petverse.promotion.utils;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.promotion.constants.PromotionConstants;

/**
 * 兑换码生成与解析算法：
 * <pre>
 * 1. 兑换码分为明文和密文，明文是 50 位二进制数，密文是长度为 10 的 Base32 编码字符串
 * 2. 兑换码的明文结构：14(校验码) + 4(新鲜值) + 32(序列号)
 *    - 序列号：单调递增的数字，通过 Redis INCR 生成
 *    - 新鲜值：优惠券 id 的最后 4 位，同一张优惠券的兑换码有相同标记
 *    - 载荷：新鲜值(4位) 拼接 序列号(32位)
 *    - 校验码：载荷按 4 位一组，每组乘以加权数求和，对 2^14 求余
 * 3. 加密过程：
 *    a. 利用优惠券 id 计算新鲜值 f；将 f 和序列号 s 拼接得到载荷 payload
 *    b. 以 f 为角标从 16 组加权码表中选一组，对 payload 做加权计算得到校验码 c
 *    c. 利用 c 的后 5 位做角标，从异或密钥表中选择一个密钥 key
 *    d. 将 payload 与 key 异或得到新 payload2；拼接明文：校验码(14位) + payload2(36位)
 *    e. Base32 编码生成兑换码
 * 4. 解密过程：Base32 解码 → 拆出校验码与载荷 → 异或还原 payload → 重算校验码比对
 * </pre>
 */
public class CodeUtil {

    /** 异或密钥表，用于最后的数据混淆 */
    private static final long[] XOR_TABLE = {
            45139281907L, 61261925523L, 58169127203L, 27031786219L,
            64169927199L, 46169126943L, 32731286209L, 52082227349L,
            59169127063L, 36169126987L, 52082200939L, 61261925739L,
            32731286563L, 27031786427L, 56169127077L, 34111865001L,
            52082216763L, 61261925663L, 56169127113L, 45139282119L,
            32731286479L, 64169927233L, 41390251661L, 59169127121L,
            64169927321L, 55139282179L, 34111864881L, 46169127031L,
            58169127221L, 61261925523L, 36169126943L, 64169927363L,
    };
    /** 新鲜值的偏移位数 */
    private static final int FRESH_BIT_OFFSET = 32;
    /** 校验码的偏移位数 */
    private static final int CHECK_CODE_BIT_OFFSET = 36;
    /** 新鲜值掩码，4 位 */
    private static final int FRESH_MASK = 0xF;
    /** 校验码掩码，14 位 */
    private static final int CHECK_CODE_MASK = 0b11111111111111;
    /** 载荷掩码，36 位 */
    private static final long PAYLOAD_MASK = 0xFFFFFFFFFL;
    /** 序列号掩码，32 位 */
    private static final long SERIAL_NUM_MASK = 0xFFFFFFFFL;
    /** 序列号加权运算的秘钥表（16 组，以新鲜值为角标选择） */
    private static final int[][] PRIME_TABLE = {
            {23, 59, 241, 61, 607, 67, 977, 1217, 1289, 1601},
            {79, 83, 107, 439, 313, 619, 911, 1049, 1237},
            {173, 211, 499, 673, 823, 941, 1039, 1213, 1429, 1259},
            {31, 293, 311, 349, 431, 577, 757, 883, 1009, 1657},
            {353, 23, 367, 499, 599, 661, 719, 929, 1301, 1511},
            {103, 179, 353, 467, 577, 691, 811, 947, 1153, 1453},
            {213, 439, 257, 313, 571, 619, 743, 829, 983, 1103},
            {31, 151, 241, 349, 607, 677, 769, 823, 967, 1049},
            {61, 83, 109, 137, 151, 521, 701, 827, 1123},
            {23, 61, 199, 223, 479, 647, 739, 811, 947, 1019},
            {31, 109, 311, 467, 613, 743, 821, 881, 1031, 1171},
            {41, 173, 367, 401, 569, 683, 761, 883, 1009, 1181},
            {127, 283, 467, 577, 661, 773, 881, 967, 1097, 1289},
            {59, 137, 257, 347, 439, 547, 641, 839, 977, 1009},
            {61, 199, 313, 421, 613, 739, 827, 941, 1087, 1307},
            {19, 127, 241, 353, 499, 607, 811, 919, 1031, 1301}
    };

    private CodeUtil() {
    }

    /**
     * 生成兑换码
     *
     * @param serialNum 递增序列号（低 32 位）
     * @param fresh     优惠券 id（取低 4 位作为新鲜值）
     * @return 兑换码
     */
    public static String generateCode(long serialNum, long fresh) {
        // 1.计算新鲜值
        fresh = fresh & FRESH_MASK;
        // 2.拼接 payload，fresh（4位） + serialNum（32位）
        long payload = fresh << FRESH_BIT_OFFSET | serialNum;
        // 3.计算验证码
        long checkCode = calcCheckCode(payload, (int) fresh);
        // 4.payload 与大质数异或，混淆数据
        payload ^= XOR_TABLE[(int) (checkCode & 0b11111)];
        // 5.拼接兑换码明文：校验码（14位） + payload（36位）
        long code = checkCode << CHECK_CODE_BIT_OFFSET | payload;
        // 6.转码
        return Base32.encode(code);
    }

    private static long calcCheckCode(long payload, int fresh) {
        // 1.获取码表
        int[] table = PRIME_TABLE[fresh];
        // 2.生成校验码：payload 每 4 位乘加权数求和，取最后 14 位
        long sum = 0;
        int index = 0;
        while (payload > 0) {
            sum += (payload & 0xf) * table[index++];
            payload >>>= 4;
        }
        return sum & CHECK_CODE_MASK;
    }

    /**
     * 解析兑换码，校验通过返回其中的序列号
     *
     * @param code 兑换码
     * @return 序列号
     */
    public static long parseCode(String code) {
        if (code == null || !code.matches(PromotionConstants.COUPON_CODE_PATTERN)) {
            // 兑换码格式错误
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效兑换码");
        }
        // 1.Base32 解码
        long num = Base32.decode(code);
        // 2.获取低 36 位 payload
        long payload = num & PAYLOAD_MASK;
        // 3.获取高 14 位校验码
        int checkCode = (int) (num >>> CHECK_CODE_BIT_OFFSET);
        // 4.载荷异或大质数，还原原始 payload
        payload ^= XOR_TABLE[(checkCode & 0b11111)];
        // 5.获取高 4 位新鲜值
        int fresh = (int) (payload >>> FRESH_BIT_OFFSET & FRESH_MASK);
        // 6.验证格式
        if (calcCheckCode(payload, fresh) != checkCode) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "无效兑换码");
        }
        return payload & SERIAL_NUM_MASK;
    }
}
