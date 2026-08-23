package com.my.petverse.common.oss;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 阿里云 OSS 配置（公共能力，各服务在自己的配置文件中添加 aliyun.oss 段即可启用）
 * 建议正式环境将密钥维护在 Nacos 配置中心，不要提交真实密钥到代码库
 */
@Data
@Component
@ConfigurationProperties(prefix = "aliyun.oss")
public class OssProperties {

    /** 地域节点，例如 oss-cn-hangzhou.aliyuncs.com */
    private String endpoint;

    /** 访问密钥ID */
    private String accessKeyId;

    /** 访问密钥Secret */
    private String accessKeySecret;

    /** 存储桶名称 */
    private String bucketName;

    /** 可选：自定义/CDN 访问域名（含协议），为空时按桶默认域名拼接 */
    private String domain;
}
