-- petverse_user 数据库初始化脚本
-- 存量库角色字段迁移脚本（仅需执行一次）：
-- ALTER TABLE `user` ADD COLUMN role VARCHAR(20) DEFAULT 'USER' COMMENT '角色 USER-普通用户 MERCHANT-商家 ADMIN-管理员' AFTER status;
-- UPDATE `user` SET role = 'USER' WHERE role IS NULL;
-- 手工指定管理员账号：UPDATE `user` SET role = 'ADMIN' WHERE username = 'admin';

CREATE TABLE IF NOT EXISTS `user`
(
    id              BIGINT       NOT NULL COMMENT '主键(雪花ID)',
    username        VARCHAR(50)  NOT NULL COMMENT '登录用户名',
    password        VARCHAR(100) NOT NULL COMMENT '登录密码(BCrypt加密)',
    nickname        VARCHAR(30)  DEFAULT NULL COMMENT '昵称',
    avatar          VARCHAR(255) DEFAULT NULL COMMENT '头像地址',
    status          TINYINT      DEFAULT 1 COMMENT '账号状态 1-正常 0-禁用',
    role            VARCHAR(20)  DEFAULT 'USER' COMMENT '角色 USER-普通用户 MERCHANT-商家 ADMIN-管理员',
    last_login_time DATETIME     DEFAULT NULL COMMENT '最近登录时间',
    create_time     DATETIME     DEFAULT NULL COMMENT '创建时间',
    update_time     DATETIME     DEFAULT NULL COMMENT '更新时间',
    deleted         TINYINT      DEFAULT 0 COMMENT '逻辑删除 0-否 1-是',
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT ='用户';
