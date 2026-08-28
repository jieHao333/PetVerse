package com.my.petverse.common.document.shop;

import lombok.Data;
import org.springframework.data.annotation.Id;
import org.springframework.data.elasticsearch.annotations.Document;
import org.springframework.data.elasticsearch.annotations.Field;
import org.springframework.data.elasticsearch.annotations.FieldType;
import org.springframework.data.elasticsearch.annotations.Setting;

import java.io.Serializable;

/**
 * 商品的 Elasticsearch 文档，用于替代数据库 LIKE 的全文检索。
 * 只保存检索、过滤、排序所需字段，展示数据仍以 MySQL 为准。
 */
@Data
@Document(indexName = "petverse_product")
@Setting(shards = 1, replicas = 0)
public class ProductDoc implements Serializable {

    /** 与 product 表主键一致 */
    @Id
    private String id;

    /** 商品名称，IK 分词后参与全文检索 */
    @Field(type = FieldType.Text, analyzer = "ik_max_word", searchAnalyzer = "ik_smart")
    private String name;

    /** 商品详情，IK 分词后参与全文检索 */
    @Field(type = FieldType.Text, analyzer = "ik_max_word", searchAnalyzer = "ik_smart")
    private String description;

    /** 所属店铺名称，支持按店铺名搜索商品 */
    @Field(type = FieldType.Text, analyzer = "ik_max_word", searchAnalyzer = "ik_smart")
    private String shopName;

    /** 商品类型：1-宠物用品 2-宠物食品 3-活体宠物 */
    @Field(type = FieldType.Integer)
    private Integer category;

    /** 所属商家ID，精确过滤 */
    @Field(type = FieldType.Long)
    private Long merchantId;

    /** 状态：0-下架 1-上架 */
    @Field(type = FieldType.Integer)
    private Integer status;

    /** 上架时间的 epoch 毫秒值，便于排序 */
    @Field(type = FieldType.Long)
    private Long createTime;
}
