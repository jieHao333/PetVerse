-- petverse_space 数据库脚本（宠域空间）
--
-- 一、已有环境迁移（原 petverse_note.note 升级为宠域空间，历史数据保留）：
-- CREATE DATABASE IF NOT EXISTS petverse_space DEFAULT CHARSET utf8mb4;
-- RENAME TABLE petverse_note.note TO petverse_space.space;
-- ALTER TABLE petverse_space.space MODIFY COLUMN title VARCHAR(100) DEFAULT NULL COMMENT '动态标题(可选)';
-- 迁移完成后再执行下方 space_media 建表语句。
--
-- 二、全新环境初始化：先创建数据库，再执行本脚本全部建表语句
-- CREATE DATABASE IF NOT EXISTS petverse_space DEFAULT CHARSET utf8mb4;
--
-- 三、已有环境升级（点赞功能）：为 space 表补充点赞数冗余列，由点赞服务定时同步，用于热度排序：
-- ALTER TABLE petverse_space.space ADD COLUMN like_count INT NOT NULL DEFAULT 0 COMMENT '点赞数(由remark-service定时同步，用于热度排序)';
-- ALTER TABLE petverse_space.space ADD KEY idx_like_count (like_count);

CREATE TABLE IF NOT EXISTS `space`
(
    id          BIGINT   NOT NULL COMMENT '主键(雪花ID)',
    title       VARCHAR(100) DEFAULT NULL COMMENT '动态标题(可选)',
    content     TEXT     NOT NULL COMMENT '动态内容',
    category    VARCHAR(50)  DEFAULT NULL COMMENT '动态分类',
    pet_id      BIGINT       DEFAULT NULL COMMENT '关联的宠物ID，可为空',
    user_id     BIGINT   NOT NULL COMMENT '所属用户ID',
    visibility  TINYINT  NOT NULL DEFAULT 0 COMMENT '可见性 0-公开 1-仅好友 2-仅自己',
    like_count  INT      NOT NULL DEFAULT 0 COMMENT '点赞数(由remark-service定时同步，用于热度排序)',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_id (user_id),
    KEY idx_pet_id (pet_id),
    KEY idx_create_time (create_time),
    KEY idx_like_count (like_count)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠域空间动态';

CREATE TABLE IF NOT EXISTS space_media
(
    id          BIGINT        NOT NULL COMMENT '主键(雪花ID)',
    space_id    BIGINT        NOT NULL COMMENT '所属动态ID',
    media_type  TINYINT       NOT NULL COMMENT '媒体类型 0-图片 1-视频',
    url         VARCHAR(1024) NOT NULL COMMENT '媒体OSS地址',
    sort_order  INT           NOT NULL DEFAULT 0 COMMENT '展示顺序，从0开始',
    create_time DATETIME DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT  DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_space_id (space_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠域空间动态媒体';
