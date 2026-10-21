package com.my.petverse.common.dto.promotion;

import com.my.petverse.common.enums.DiscountType;
import com.my.petverse.common.enums.ObtainType;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;
import org.hibernate.validator.constraints.Range;

import java.io.Serializable;
import java.util.List;

/**
 * 新增/修改优惠券表单参数（金额字段单位为分）
 */
@Data
public class CouponFormDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 优惠券 id，新增不需要传，更新必填 */
    private Long id;

    /** 优惠券名称 */
    @NotNull(message = "优惠券名称不能为空")
    @Size(max = 20, min = 4, message = "优惠券名称长度错误")
    private String name;

    /** 是否限定使用范围：true-限定 false-不限定 */
    private Boolean specific;

    /** 使用范围类型：0-不限范围 1-限定商品分类 2-限定指定商品（specific 为 true 时有效） */
    private Integer scopeType;

    /** 使用范围业务 id 集合（分类 id 或商品 id） */
    private List<Long> scopes;

    /** 折扣类型：1-每满减 2-折扣 3-无门槛 4-满减 */
    @NotNull(message = "优惠券折扣类型不能为空")
    private DiscountType discountType;

    /** 折扣门槛（分），0 代表无门槛 */
    private Integer thresholdAmount;

    /** 折扣值：满减填抵扣金额（分）；打折填折扣值（80 表示 8 折） */
    private Integer discountValue;

    /** 最大优惠金额（分） */
    private Integer maxDiscountAmount;

    /** 优惠券总量 */
    @Range(max = 5000, min = 1, message = "优惠券总量必须在1~5000")
    private Integer totalNum;

    /** 每人领取上限 */
    @Range(max = 10, min = 1, message = "每人限领数量必须在1~10")
    private Integer userLimit;

    /** 获取方式：1-手动领取 2-发放兑换码 */
    @NotNull(message = "领取方式不能为空")
    private ObtainType obtainWay;
}
