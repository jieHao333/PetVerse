-- petverse_shop 数据库初始化脚本
-- CREATE DATABASE IF NOT EXISTS petverse_shop DEFAULT CHARSET utf8mb4;

-- 商家入驻申请单（审批流水，驳回后可修改重提）
CREATE TABLE IF NOT EXISTS merchant_apply
(
    id            BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    user_id       BIGINT       NOT NULL COMMENT '申请人用户ID',
    shop_name     VARCHAR(50)  NOT NULL COMMENT '店铺名称',
    contact_name  VARCHAR(30)  NOT NULL COMMENT '联系人姓名',
    contact_phone VARCHAR(20)  NOT NULL COMMENT '联系电话',
    license_no    VARCHAR(50)  NOT NULL COMMENT '营业执照号',
    license_url   VARCHAR(255) NOT NULL COMMENT '营业执照图片(OSS地址)',
    description   VARCHAR(500) DEFAULT NULL COMMENT '店铺简介',
    status        TINYINT      DEFAULT 0 COMMENT '审批状态 0-待审核 1-已通过 2-已驳回',
    reject_reason VARCHAR(200) DEFAULT NULL COMMENT '驳回原因',
    audit_user_id BIGINT       DEFAULT NULL COMMENT '审批管理员ID',
    audit_time    DATETIME     DEFAULT NULL COMMENT '审批时间',
    create_time   DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time   DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted       TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_id (user_id),
    KEY idx_status (status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='商家入驻申请单';

-- 商家店铺（入驻审批通过后生成）
CREATE TABLE IF NOT EXISTS merchant
(
    id            BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    user_id       BIGINT       NOT NULL COMMENT '店主用户ID',
    apply_id      BIGINT       NOT NULL COMMENT '来源申请单ID',
    shop_name     VARCHAR(50)  NOT NULL COMMENT '店铺名称',
    shop_logo     VARCHAR(255) DEFAULT NULL COMMENT '店铺LOGO',
    description   VARCHAR(500) DEFAULT NULL COMMENT '店铺简介',
    contact_phone VARCHAR(20)  DEFAULT NULL COMMENT '联系电话',
    status        TINYINT      DEFAULT 1 COMMENT '店铺状态 1-营业 0-封禁',
    create_time   DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time   DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted       TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_id (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='商家店铺';

-- 商品（宠物用品/食品与活体宠物）
CREATE TABLE IF NOT EXISTS product
(
    id          BIGINT         NOT NULL COMMENT '主键(雪花ID)',
    merchant_id BIGINT         NOT NULL COMMENT '所属商家ID',
    name        VARCHAR(100)   NOT NULL COMMENT '商品名称',
    category    TINYINT        NOT NULL COMMENT '商品类型 1-宠物用品 2-宠物食品 3-活体宠物',
    price       DECIMAL(10, 2) NOT NULL COMMENT '售价(元)',
    stock       INT            DEFAULT 0 COMMENT '库存',
    image_url   VARCHAR(255)   DEFAULT NULL COMMENT '商品主图',
    description VARCHAR(1000)  DEFAULT NULL COMMENT '商品详情',
    status      TINYINT        DEFAULT 0 COMMENT '状态 0-下架 1-上架',
    create_time DATETIME       DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME       DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT        DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_merchant_id (merchant_id),
    KEY idx_category_status (category, status)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='商品';

-- 购物车条目（同一商品重复加购时累加数量；移除走物理删除，避免已删条目占用唯一索引）
CREATE TABLE IF NOT EXISTS cart_item
(
    id          BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    user_id     BIGINT   NOT NULL COMMENT '买家用户ID',
    product_id  BIGINT   NOT NULL COMMENT '商品ID',
    quantity    INT      NOT NULL DEFAULT 1 COMMENT '购买数量',
    create_time DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    -- (user_id, product_id) 唯一：并发加购由数据库兜底，同一用户同一商品只会存在一行
    UNIQUE KEY uk_user_product (user_id, product_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='购物车条目';

-- 存量库升级（唯一索引建立前需先清理历史逻辑删除行，否则已删条目会占用索引槽位）：
-- DELETE FROM cart_item WHERE deleted = 1;
-- ALTER TABLE cart_item ADD UNIQUE KEY uk_user_product (user_id, product_id);
-- ALTER TABLE cart_item DROP INDEX idx_user_id;   -- 已被 uk_user_product 前缀覆盖，减少一次索引维护

-- 订单（order 为 MySQL 保留字，表名加 shop_ 前缀；目前仅支持到店自取，pickup_type 预留配送扩展）
CREATE TABLE IF NOT EXISTS shop_order
(
    id           BIGINT         NOT NULL COMMENT '主键(雪花ID)',
    order_no     VARCHAR(32)    NOT NULL COMMENT '订单号',
    user_id      BIGINT         NOT NULL COMMENT '买家用户ID',
    merchant_id  BIGINT         NOT NULL COMMENT '商家ID（一单一店）',
    total_amount DECIMAL(10, 2) NOT NULL COMMENT '订单总金额(元)',
    status       TINYINT        DEFAULT 0 COMMENT '订单状态 0-待支付 1-待取货 2-已完成 3-已取消',
    pickup_type  TINYINT        DEFAULT 1 COMMENT '取货方式 1-到店自取（预留 2-外卖配送）',
    pickup_code  VARCHAR(10)    DEFAULT NULL COMMENT '取货码（支付后生成，到店核销凭证）',
    remark       VARCHAR(200)   DEFAULT NULL COMMENT '买家备注',
    pay_time     DATETIME       DEFAULT NULL COMMENT '支付时间',
    finish_time  DATETIME       DEFAULT NULL COMMENT '完成时间（到店核销时间）',
    cancel_time  DATETIME       DEFAULT NULL COMMENT '取消时间',
    create_time  DATETIME       DEFAULT NULL COMMENT '创建时间',
    update_time  DATETIME       DEFAULT NULL COMMENT '更新时间',
    deleted      TINYINT        DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no),
    KEY idx_user_id (user_id),
    KEY idx_merchant_id (merchant_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='订单';

-- 商品评价（仅已完成订单的买家可评价，同一用户对同一商品仅能评价一次；支持图片/视频晒单）
CREATE TABLE IF NOT EXISTS product_review
(
    id          BIGINT        NOT NULL COMMENT '主键(雪花ID)',
    product_id  BIGINT        NOT NULL COMMENT '商品ID',
    merchant_id BIGINT        NOT NULL COMMENT '商家ID（冗余，支持按店铺维度统计）',
    user_id     BIGINT        NOT NULL COMMENT '评价人用户ID',
    order_id    BIGINT        NOT NULL COMMENT '来源订单ID（已完成订单，评价资格凭证）',
    rating      TINYINT       NOT NULL COMMENT '评分 1~5 星',
    content     VARCHAR(500)  DEFAULT NULL COMMENT '评价内容',
    image_urls  VARCHAR(1500) DEFAULT NULL COMMENT '评价图片OSS地址，逗号分隔（最多9张）',
    video_url   VARCHAR(255)  DEFAULT NULL COMMENT '评价视频OSS地址（最多1个）',
    create_time DATETIME      DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME      DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT       DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_product_user (product_id, user_id),
    KEY idx_product_rating (product_id, rating)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='商品评价';

-- 商品评价回复（所有登录用户可在评价下自由互动，支持回复某条回复）
CREATE TABLE IF NOT EXISTS product_review_reply
(
    id            BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    review_id     BIGINT       NOT NULL COMMENT '评价ID',
    user_id       BIGINT       NOT NULL COMMENT '回复人用户ID',
    reply_user_id BIGINT       DEFAULT NULL COMMENT '被回复人用户ID（回复某条回复时记录，平铺展示）',
    content       VARCHAR(500) NOT NULL COMMENT '回复内容',
    create_time   DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time   DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted       TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_review_id (review_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='商品评价回复';

-- 订单明细（下单时商品快照，不随商品修改变化）
CREATE TABLE IF NOT EXISTS shop_order_item
(
    id            BIGINT         NOT NULL COMMENT '主键(雪花ID)',
    order_id      BIGINT         NOT NULL COMMENT '订单ID',
    product_id    BIGINT         NOT NULL COMMENT '商品ID',
    product_name  VARCHAR(100)   NOT NULL COMMENT '商品名称快照',
    product_image VARCHAR(255)   DEFAULT NULL COMMENT '商品主图快照',
    price         DECIMAL(10, 2) NOT NULL COMMENT '成交单价(元)',
    quantity      INT            NOT NULL COMMENT '购买数量',
    amount        DECIMAL(10, 2) NOT NULL COMMENT '小计金额(元)',
    create_time   DATETIME       DEFAULT NULL COMMENT '创建时间',
    update_time   DATETIME       DEFAULT NULL COMMENT '更新时间',
    deleted       TINYINT        DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_order_id (order_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='订单明细';
