package com.my.petverse.common.entity.pet;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;

/**
 * 用户宠物实体，对应数据库表 pet
 * 一个用户拥有一个宠物，作为社交形象
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("pet")
public class Pet extends BaseEntity {

    /** 宠物名称 */
    private String name;

    /** 物种，如：猫、狗 */
    private String species;

    /** 品种 */
    private String breed;

    /** 年龄 */
    private Integer age;

    /** 宠物描述 */
    private String description;

    /** 形象图片地址 */
    private String imageUrl;

    /** 所属用户ID（唯一，一人一宠） */
    private Long userId;

    /** 宠物等级，范围 1-100 */
    private Integer level;

    /** 当前等级已获得的经验值（升级后清零重计） */
    private Long exp;

    /** 最近签到日期，用于判断当天是否已签到及连续天数 */
    private LocalDate lastSignDate;

    /** 连续签到天数 */
    private Integer signStreak;
}
