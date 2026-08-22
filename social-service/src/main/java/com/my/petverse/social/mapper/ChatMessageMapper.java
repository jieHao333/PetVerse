package com.my.petverse.social.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.social.ChatMessage;
import org.apache.ibatis.annotations.Mapper;

/**
 * 聊天消息数据访问接口
 */
@Mapper
public interface ChatMessageMapper extends BaseMapper<ChatMessage> {
}
