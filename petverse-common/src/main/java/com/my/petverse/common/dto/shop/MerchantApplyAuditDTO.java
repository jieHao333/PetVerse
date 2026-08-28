package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 商家入驻申请审批请求参数（管理员）
 */
@Data
public class MerchantApplyAuditDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 申请单ID */
    @NotNull(message = "申请单ID不能为空")
    private Long id;

    /** 审批结果：true-通过 false-驳回 */
    @NotNull(message = "审批结果不能为空")
    private Boolean approved;

    /** 驳回原因（驳回时必填，Service 内校验） */
    @Size(max = 200, message = "驳回原因不能超过200个字符")
    private String rejectReason;
}
