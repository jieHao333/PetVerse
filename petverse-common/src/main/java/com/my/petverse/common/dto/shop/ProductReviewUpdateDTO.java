package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.util.List;

/**
 * 修改商品评价参数（仅评价人本人可修改，评分与内容整体覆盖）
 */
@Data
public class ProductReviewUpdateDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 评价ID */
    @NotNull(message = "评价ID不能为空")
    private Long id;

    /** 评分：1~5 星 */
    @NotNull(message = "请选择评分")
    @Min(value = 1, message = "评分不能小于1星")
    @Max(value = 5, message = "评分不能大于5星")
    private Integer rating;

    /** 评价内容（选填） */
    @Size(max = 500, message = "评价内容不能超过500字")
    private String content;

    /** 评价图片OSS地址列表（最多9张，传空数组表示清空图片） */
    @Size(max = 9, message = "评价图片最多上传9张")
    private List<String> imageUrls;

    /** 评价视频OSS地址（最多1个，传空串表示清空视频） */
    private String videoUrl;
}
