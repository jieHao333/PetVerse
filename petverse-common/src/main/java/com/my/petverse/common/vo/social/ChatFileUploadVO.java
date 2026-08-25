package com.my.petverse.common.vo.social;

import lombok.Data;

import java.io.Serializable;

/**
 * 聊天文件上传结果：返回 OSS 地址、消息类型与文件原始名称
 */
@Data
public class ChatFileUploadVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 文件的 OSS 公网访问地址 */
    private String url;

    /** 消息类型 1-图片 2-文件（按扩展名判定） */
    private Integer msgType;

    /** 文件原始名称 */
    private String fileName;
}
