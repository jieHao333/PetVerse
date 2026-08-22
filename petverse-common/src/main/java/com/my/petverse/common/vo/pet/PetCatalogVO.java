package com.my.petverse.common.vo.pet;

import lombok.Data;

import java.io.Serializable;

/**
 * 宠物图鉴返回实体
 */
@Data
public class PetCatalogVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 图鉴ID */
    private Long id;

    /** 宠物名称 */
    private String name;

    /** 物种 */
    private String species;

    /** 品种 */
    private String breed;

    /** 稀有度编码：1-普通 2-稀有 3-传说 */
    private Integer rarity;

    /** 稀有度名称，如：普通/稀有/传说 */
    private String rarityName;

    /** 宠物描述 */
    private String description;

    /** 宠物图片地址 */
    private String imageUrl;
}
