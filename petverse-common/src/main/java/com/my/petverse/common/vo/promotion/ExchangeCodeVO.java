package com.my.petverse.common.vo.promotion;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 兑换码信息
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class ExchangeCodeVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 兑换码序列号 */
    private Integer id;

    /** 兑换码 */
    private String code;
}
