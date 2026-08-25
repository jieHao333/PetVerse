package com.my.petverse.common.oss;

import com.aliyun.oss.ClientBuilderConfiguration;
import com.aliyun.oss.ClientException;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.OSSException;
import com.aliyun.oss.model.ObjectMetadata;
import com.my.petverse.common.exception.BusinessException;
import jakarta.annotation.PreDestroy;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.Locale;

/**
 * 阿里云 OSS 存储服务（公共能力）
 * 各服务在自己的配置文件中填写 aliyun.oss 参数后即可注入使用，负责文件上传并返回可访问的 URL
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class OssService {

    /** 上传失败后的最大重试次数（首次之外） */
    private static final int MAX_RETRY_TIMES = 2;

    private final OssProperties ossProperties;

    /** 复用的 OSS 客户端：避免每次上传新建客户端造成连接池与线程资源泄漏 */
    private volatile OSS ossClient;

    /**
     * 上传文件到 OSS
     *
     * @param objectKey 存储路径（如 avatar/20260823/xxx.png）
     * @param file      上传的文件
     * @return 文件的公网访问 URL
     */
    public String upload(String objectKey, MultipartFile file) {
        OSS client = getOssClient();
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentType(resolveContentType(file));
        metadata.setContentLength(file.getSize());
        byte[] bytes;
        try {
            bytes = file.getBytes();
        } catch (IOException e) {
            throw new BusinessException("文件读取失败，请重新选择文件");
        }
        // 网络抖动与 OSS 偶发错误时自动重试，降低偶发上传失败概率
        OSSException lastOssError = null;
        ClientException lastClientError = null;
        for (int attempt = 0; attempt <= MAX_RETRY_TIMES; attempt++) {
            try {
                client.putObject(ossProperties.getBucketName(), objectKey,
                        new ByteArrayInputStream(bytes), metadata);
                return buildAccessUrl(objectKey);
            } catch (OSSException e) {
                lastOssError = e;
                log.warn("上传文件到 OSS 失败（第{}次），objectKey={}", attempt + 1, objectKey, e);
            } catch (ClientException e) {
                lastClientError = e;
                log.warn("上传文件到 OSS 网络异常（第{}次），objectKey={}", attempt + 1, objectKey, e);
            }
            try {
                Thread.sleep(300L * (attempt + 1));
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                throw new BusinessException("文件上传被中断，请重试");
            }
        }
        log.error("上传文件到 OSS 最终失败，objectKey={}", objectKey,
                lastOssError != null ? lastOssError : lastClientError);
        throw new BusinessException("文件上传失败，请稍后重试");
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

    /**
     * 解析 Content-Type：浏览器未正确识别时按扩展名兜底，
     * 避免 octet-stream 导致部分浏览器不渲染图片
     */
    private String resolveContentType(MultipartFile file) {
        String contentType = file.getContentType();
        if (StringUtils.hasText(contentType) && !"application/octet-stream".equals(contentType)) {
            return contentType;
        }
        String name = file.getOriginalFilename() == null ? "" : file.getOriginalFilename().toLowerCase(Locale.ROOT);
        if (name.endsWith(".jpg") || name.endsWith(".jpeg")) {
            return "image/jpeg";
        }
        if (name.endsWith(".png")) {
            return "image/png";
        }
        if (name.endsWith(".webp")) {
            return "image/webp";
        }
        if (name.endsWith(".gif")) {
            return "image/gif";
        }
        if (name.endsWith(".mp4")) {
            return "video/mp4";
        }
        return "application/octet-stream";
    }

    /** 获取复用的 OSS 客户端；未配置密钥时直接抛出明确提示，避免带着占位符调云端接口 */
    private OSS getOssClient() {
        if (!StringUtils.hasText(ossProperties.getAccessKeyId())
                || "your-access-key-id".equals(ossProperties.getAccessKeyId())) {
            throw new BusinessException("OSS 未配置，请在当前服务配置中填写 aliyun.oss 相关参数");
        }
        //拷贝成员变量到局部变量，提升性能，外层无锁快速判断
        OSS client = ossClient;
        if (client == null) {
            synchronized (this) {
                client = ossClient;
                if (client == null) {
                    ClientBuilderConfiguration conf = new ClientBuilderConfiguration();
                    conf.setConnectionTimeout(5000);
                    conf.setSocketTimeout(60000);
                    conf.setConnectionRequestTimeout(3000);
                    conf.setMaxErrorRetry(1);
                    client = new OSSClientBuilder().build(
                            ossProperties.getEndpoint(),
                            ossProperties.getAccessKeyId(),
                            ossProperties.getAccessKeySecret(),
                            conf);
                    ossClient = client;
                }
            }
        }
        return client;
    }

    /** 服务关闭时释放客户端连接池资源 */
    @PreDestroy
    public void shutdown() {
        if (ossClient != null) {
            ossClient.shutdown();
        }
    }
}
