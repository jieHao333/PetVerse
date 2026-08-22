package com.my.petverse.common.entity.pet;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * 宠物图鉴实体，对应数据库表 pet_catalog
 * 新用户领取宠物时可随机抽取或自选的宠物池
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("pet_catalog")
public class PetCatalog extends BaseEntity {

    /** 宠物名称 */
    private String name;

    /** 物种，如：猫、狗 */
    private String species;

    /** 品种 */
    private String breed;

    /** 稀有度：1-普通 2-稀有 3-传说，影响随机抽取概率 */
    private Integer rarity;

    /** 宠物描述 */
    private String description;

    /** 宠物图片地址 */
    private String imageUrl;
}
