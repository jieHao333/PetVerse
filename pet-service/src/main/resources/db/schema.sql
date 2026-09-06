-- petverse_pet 数据库初始化脚本
-- 存量库迁移脚本（仅需执行一次）：
-- 1) 多宠物改造：
-- ALTER TABLE pet DROP INDEX uk_user_id;
-- ALTER TABLE pet ADD INDEX idx_user_id (user_id);
-- 2) 宠物分类（真实/虚拟）与真实档案字段，并移除出场机制：
-- ALTER TABLE pet ADD COLUMN type VARCHAR(10) DEFAULT 'VIRTUAL' COMMENT '类型 REAL-真实 VIRTUAL-虚拟' AFTER user_id;
-- ALTER TABLE pet ADD COLUMN adoption_date DATE DEFAULT NULL COMMENT '收养时间(真实宠物)' AFTER sign_streak;
-- ALTER TABLE pet ADD COLUMN gender TINYINT DEFAULT NULL COMMENT '性别 1-弟弟 2-妹妹(真实宠物)' AFTER adoption_date;
-- ALTER TABLE pet ADD COLUMN birthday DATE DEFAULT NULL COMMENT '生日(真实宠物)' AFTER gender;
-- ALTER TABLE pet ADD COLUMN sterilized TINYINT DEFAULT 0 COMMENT '是否绝育 0-否 1-是(真实宠物)' AFTER birthday;
-- UPDATE pet SET type = 'VIRTUAL' WHERE type IS NULL;
-- ALTER TABLE pet DROP COLUMN active;

CREATE TABLE IF NOT EXISTS pet
(
    id             BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    user_id        BIGINT       NOT NULL COMMENT '所属用户ID',
    type           VARCHAR(10)  DEFAULT 'VIRTUAL' COMMENT '类型 REAL-真实 VIRTUAL-虚拟',
    name           VARCHAR(50)  NOT NULL COMMENT '宠物名称',
    species        VARCHAR(50)  DEFAULT NULL COMMENT '物种',
    breed          VARCHAR(50)  DEFAULT NULL COMMENT '品种',
    age            INT          DEFAULT 0 COMMENT '年龄',
    description    VARCHAR(500) DEFAULT NULL COMMENT '描述',
    image_url      VARCHAR(255) DEFAULT NULL COMMENT '形象图片',
    level          INT          DEFAULT 1 COMMENT '等级(1-100)',
    exp            BIGINT       DEFAULT 0 COMMENT '当前等级经验值',
    last_sign_date DATE         DEFAULT NULL COMMENT '最近签到日期',
    sign_streak    INT          DEFAULT 0 COMMENT '连续签到天数',
    adoption_date  DATE         DEFAULT NULL COMMENT '收养时间(真实宠物)',
    gender         TINYINT      DEFAULT NULL COMMENT '性别 1-弟弟 2-妹妹(真实宠物)',
    birthday       DATE         DEFAULT NULL COMMENT '生日(真实宠物)',
    sterilized     TINYINT      DEFAULT 0 COMMENT '是否绝育 0-否 1-是(真实宠物)',
    create_time    DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time    DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted        TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    KEY idx_user_id (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户宠物';

CREATE TABLE IF NOT EXISTS pet_catalog
(
    id          BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    name        VARCHAR(50)  NOT NULL COMMENT '宠物名称',
    species     VARCHAR(50)  DEFAULT NULL COMMENT '物种',
    breed       VARCHAR(50)  DEFAULT NULL COMMENT '品种',
    rarity      TINYINT      DEFAULT 1 COMMENT '稀有度 1-普通 2-稀有 3-传说',
    description VARCHAR(500) DEFAULT NULL COMMENT '描述',
    image_url   VARCHAR(255) DEFAULT NULL COMMENT '图片',
    create_time DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted     TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='宠物图鉴';

-- 图鉴种子数据
INSERT INTO pet_catalog (id, name, species, breed, rarity, description, image_url, create_time, update_time)
VALUES (1, '小橘猫', '猫', '中华田园猫', 1, '最常见的橘色小猫，性格粘人', '/images/pet/cat-orange.png', NOW(), NOW()),
       (2, '小狸花', '猫', '中华狸花猫', 1, '身手矫健的小花猫', '/images/pet/cat-tabby.png', NOW(), NOW()),
       (3, '泰迪犬', '狗', '贵宾犬', 1, '活泼可爱的卷毛小狗', '/images/pet/dog-poodle.png', NOW(), NOW()),
       (4, '哈士奇', '狗', '哈士奇', 1, '天生表情包的雪橇犬', '/images/pet/dog-husky.png', NOW(), NOW()),
       (5, '金毛犬', '狗', '金毛寻回犬', 2, '温顺忠诚的暖男大狗', '/images/pet/dog-golden.png', NOW(), NOW()),
       (6, '蓝猫', '猫', '英国短毛猫', 2, '优雅高冷的贵族猫', '/images/pet/cat-british.png', NOW(), NOW()),
       (7, '布偶猫', '猫', '布偶猫', 3, '传说级的仙女猫，温柔亲人', '/images/pet/cat-ragdoll.png', NOW(), NOW()),
       (8, '德文卷毛猫', '猫', '德文卷毛猫', 3, '精灵般的外星猫咪', '/images/pet/cat-devon.png', NOW(), NOW()),
       (9, '柯基犬', '狗', '威尔士柯基', 2, '小短腿大屁股的可爱犬', '/images/pet/dog-corgi.png', NOW(), NOW());
