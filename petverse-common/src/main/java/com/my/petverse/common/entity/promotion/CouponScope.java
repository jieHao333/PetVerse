package com.my.petverse.common.entity.promotion;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;

/**
 * 优惠券作用范围实体，对应数据库表 coupon_scope
 */
@Data
@Accessors(chain = true)
@TableName("coupon_scope")
public class CouponScope implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 主键，自增 */
    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    /** 范围限定类型：1-商品分类 2-指定商品 */
    private Integer type;

    /** 优惠券 id */
    private Long couponId;

    /** 作用范围的业务 id（分类 id 或商品 id） */
    private Long bizId;
}
