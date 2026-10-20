package com.my.petverse.common.mq.message;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 商家撤销冷静期结束事件：消费方为 user-service，将店主角色降级为普通用户
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class MerchantWithdrawnMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商家ID（幂等去重键） */
    private Long merchantId;

    /** 店主用户ID */
    private Long userId;
}
