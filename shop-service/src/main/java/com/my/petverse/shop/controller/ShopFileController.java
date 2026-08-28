package com.my.petverse.shop.controller;

import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.oss.OssService;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import lombok.RequiredArgsConstructor;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Set;
import java.util.UUID;

/**
 * 商城图片上传接口控制器（营业执照/商品主图，存储到阿里云OSS）
 */
@RestController
@RequestMapping("/shop/file")
@RequiredArgsConstructor
public class ShopFileController {

    /** 支持的图片扩展名 */
    private static final Set<String> IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "webp", "gif");

    /** 图片大小上限：5MB */
    private static final long MAX_IMAGE_SIZE = 5 * 1024 * 1024L;

    /** 存储路径日期目录格式 */
    private static final DateTimeFormatter DATE_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final OssService ossService;

    /** 上传图片（jpg/jpeg/png/webp/gif，≤5MB），返回OSS公网地址 */
    @PostMapping
    public Result<String> upload(@RequestParam("file") MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "上传文件不能为空");
        }
        String extension = getExtension(file.getOriginalFilename());
        if (!IMAGE_EXTENSIONS.contains(extension)) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅支持 jpg/jpeg/png/webp/gif 图片");
        }
        if (file.getSize() > MAX_IMAGE_SIZE) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "图片大小不能超过5MB");
        }
        String objectKey = "shop/" + LocalDate.now().format(DATE_FORMAT) + "/"
                + UUID.randomUUID().toString().replace("-", "") + "." + extension;
        return Result.success(ossService.upload(objectKey, file));
    }

    /** 提取小写扩展名，无扩展名返回空串 */
    private String getExtension(String filename) {
        if (!StringUtils.hasText(filename)) {
            return "";
        }
        int dotIndex = filename.lastIndexOf('.');
        if (dotIndex < 0 || dotIndex == filename.length() - 1) {
            return "";
        }
        return filename.substring(dotIndex + 1).toLowerCase();
    }
}
