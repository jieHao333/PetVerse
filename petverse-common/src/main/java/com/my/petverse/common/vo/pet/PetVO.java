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

    /** 类型 REAL-真实 VIRTUAL-虚拟 */
    private String type;

    /** 类型中文名（真实宠物/虚拟宠物） */
    private String typeName;

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

    /** 性别编码 1-弟弟 2-妹妹（真实宠物） */
    private Integer gender;

    /** 性别中文名（弟弟/妹妹） */
    private String genderName;

    /** 生日（真实宠物） */
    private LocalDate birthday;

    /** 是否绝育（真实宠物） */
    private Boolean sterilized;

    /** 收养时间（真实宠物） */
    private LocalDate adoptionDate;

    /** 身份卡签发日期，非空表示已签发宠物身份证 */
    private LocalDate cardIssueDate;

    /** 健康-体重（猫/狗） */
    private String weight;

    /** 健康-BCS 体况评分（猫/狗） */
    private String bcs;

    /** 健康-驱虫（猫/狗） */
    private String deworming;

    /** 健康-特殊时期（猫/狗） */
    private String specialPeriod;

    /** 健康-疫苗（猫/狗） */
    private String vaccine;

    /** 健康-养育方式（猫/狗） */
    private String rearingMethod;

    /** 健康-病史（猫/狗） */
    private String medicalHistory;

    /** 创建时间 */
    private LocalDateTime createTime;
}
