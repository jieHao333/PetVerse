package com.my.petverse.common.entity.pet;

import com.baomidou.mybatisplus.annotation.TableName;
import com.my.petverse.common.entity.BaseEntity;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.time.LocalDate;

/**
 * 用户宠物实体，对应数据库表 pet
 * 一个用户可拥有多只宠物，分真实宠物（用户登记的实际饲养宠物）与虚拟宠物（图鉴抽卡领养）两类
 */
@Data
@EqualsAndHashCode(callSuper = true)
@TableName("pet")
public class Pet extends BaseEntity {

    /** 类型 REAL-真实 VIRTUAL-虚拟，取值见 PetType */
    private String type;

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

    /** 所属用户ID */
    private Long userId;

    /** 宠物等级，范围 1-100 */
    private Integer level;

    /** 当前等级已获得的经验值（升级后清零重计） */
    private Long exp;

    /** 最近签到日期，用于判断当天是否已签到及连续天数 */
    private LocalDate lastSignDate;

    /** 连续签到天数 */
    private Integer signStreak;

    /** 收养时间（真实宠物） */
    private LocalDate adoptionDate;

    /** 性别 1-弟弟 2-妹妹（真实宠物），取值见 PetGender */
    private Integer gender;

    /** 生日（真实宠物） */
    private LocalDate birthday;

    /** 是否绝育 0-否 1-是（真实宠物） */
    private Integer sterilized;

    /** 身份卡签发日期，非空表示已发放宠物身份证 */
    private LocalDate cardIssueDate;
}
