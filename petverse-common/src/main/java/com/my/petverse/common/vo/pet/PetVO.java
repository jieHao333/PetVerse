package com.my.petverse.common.vo.pet;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 宠物返回实体
 */
@Data
public class PetVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 宠物ID */
    private Long id;

    /** 宠物名称 */
    private String name;

    /** 物种 */
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

    /** 当前等级已获得的经验值 */
    private Long exp;

    /** 升到下一级所需经验值，满级时为 0 */
    private Long nextLevelExp;

    /** 连续签到天数 */
    private Integer signStreak;

    /** 最近签到日期 */
    private LocalDate lastSignDate;

    /** 创建时间 */
    private LocalDateTime createTime;
}
