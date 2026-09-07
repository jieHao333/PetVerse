package com.my.petverse.social.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.social.ChatConversation;
import org.apache.ibatis.annotations.Mapper;

/**
 * 聊天会话数据访问接口
 */
@Mapper
public interface ChatConversationMapper extends BaseMapper<ChatConversation> {
}
