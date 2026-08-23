package com.my.petverse.common.oss;

import com.aliyun.oss.ClientException;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.OSSException;
import com.aliyun.oss.model.ObjectMetadata;
import com.my.petverse.common.exception.BusinessException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;

/**
 * 阿里云 OSS 存储服务（公共能力）
 * 各服务在自己的配置文件中填写 aliyun.oss 参数后即可注入使用，负责文件上传并返回可访问的 URL
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class OssService {

    private final OssProperties ossProperties;

    /**
     * 上传文件到 OSS
     *
     * @param objectKey 存储路径（如 avatar/20260823/xxx.png）
     * @param file      上传的文件
     * @return 文件的公网访问 URL
     */
    public String upload(String objectKey, MultipartFile file) {
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentType(file.getContentType());
        try (InputStream input = file.getInputStream()) {
            getOssClient().putObject(ossProperties.getBucketName(), objectKey, input, metadata);
        } catch (OSSException | ClientException e) {
            log.error("上传文件到 OSS 失败，objectKey={}", objectKey, e);
            throw new BusinessException("文件上传失败，请稍后重试");
        } catch (IOException e) {
            throw new BusinessException("文件读取失败，请重新选择图片");
        }
        return buildAccessUrl(objectKey);
    }

    /** 拼接访问 URL：优先使用自定义域名，否则按 桶名.地域节点 默认域名 */
    private String buildAccessUrl(String objectKey) {
        String domain = ossProperties.getDomain();
        if (StringUtils.hasText(domain)) {
            return domain.endsWith("/") ? domain + objectKey : domain + "/" + objectKey;
        }
        String endpoint = ossProperties.getEndpoint().replaceFirst("https?://", "");
        return "https://" + ossProperties.getBucketName() + "." + endpoint + "/" + objectKey;
    }

    /** 创建 OSS 客户端；未配置密钥时直接抛出明确提示，避免带着占位符调云端接口 */
    private OSS getOssClient() {
        if (!StringUtils.hasText(ossProperties.getAccessKeyId())
                || "your-access-key-id".equals(ossProperties.getAccessKeyId())) {
            throw new BusinessException("OSS 未配置，请在当前服务配置中填写 aliyun.oss 相关参数");
        }
        return new OSSClientBuilder().build(
                ossProperties.getEndpoint(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret());
    }
}
