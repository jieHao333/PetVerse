package com.my.petverse.common.document.space;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.elasticsearch.annotations.Document;
import org.springframework.data.elasticsearch.annotations.Field;
import org.springframework.data.elasticsearch.annotations.FieldType;
import org.springframework.data.elasticsearch.annotations.Setting;

import java.io.Serializable;

/**
 * 宠域空间动态的 Elasticsearch 文档，用于替代数据库 LIKE 的全文检索。
 * 只保存检索、过滤、排序所需字段，展示数据仍以 MySQL 为准。
 */
@Data
@Document(indexName = "petverse_space")
@Setting(shards = 1, replicas = 0)
public class SpaceDoc implements Serializable {

    /** 与 space 表主键一致 */
    @Id
    private String id;

    /** 动态标题，IK 分词后参与全文检索 */
    @Field(type = FieldType.Text, analyzer = "ik_max_word", searchAnalyzer = "ik_smart")
    private String title;

    /** 动态内容，IK 分词后参与全文检索 */
    @Field(type = FieldType.Text, analyzer = "ik_max_word", searchAnalyzer = "ik_smart")
    private String content;

    /** 动态分类，精确过滤 */
    @Field(type = FieldType.Keyword)
    private String category;

    /** 关联的宠物ID，精确过滤 */
    @Field(type = FieldType.Long)
    private Long petId;

    /** 所属用户ID，用于可见性判定与精确过滤 */
    @Field(type = FieldType.Long)
    private Long userId;

    /** 可见性：0-公开 1-仅好友 2-仅自己 */
    @Field(type = FieldType.Integer)
    private Integer visibility;

    /** 点赞数，支撑热度排序 */
    @Field(type = FieldType.Integer)
    private Integer likeCount;

    /** 发布时间的 epoch 毫秒值，便于范围过滤与排序 */
    @Field(type = FieldType.Long)
    private Long createTime;
}
