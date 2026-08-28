package com.my.petverse.common.dto.shop;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;

/**
 * 新增商品请求参数（商家）
 */
@Data
public class ProductSaveDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 商品名称 */
    @NotBlank(message = "商品名称不能为空")
    @Size(max = 100, message = "商品名称不能超过100个字符")
    private String name;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物 */
    @NotNull(message = "商品类型不能为空")
    private Integer category;

    /** 售价(元) */
    @NotNull(message = "售价不能为空")
    @DecimalMin(value = "0.01", message = "售价不能低于0.01元")
    private BigDecimal price;

    /** 库存 */
    @NotNull(message = "库存不能为空")
    @Min(value = 0, message = "库存不能小于0")
    private Integer stock;

    /** 商品主图 */
    @Size(max = 255, message = "商品主图地址过长")
    private String imageUrl;

    /** 商品详情 */
    @Size(max = 1000, message = "商品详情不能超过1000个字符")
    private String description;
}
