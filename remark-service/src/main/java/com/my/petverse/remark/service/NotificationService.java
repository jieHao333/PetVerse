package com.my.petverse.remark.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.base.BasePageQuery;
import com.my.petverse.common.entity.remark.Notification;
import com.my.petverse.common.mq.message.NotifyMessage;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.remark.NotificationVO;

/**
 * 站内通知服务接口：统一落库通知事件，并向用户提供查询与已读操作
 */
public interface NotificationService extends IService<Notification> {

    /**
     * 由通知事件落库一条通知（消费端调用）
     *
     * @param message 通知事件
     * @return 是否成功
     */
    boolean saveFromMessage(NotifyMessage message);

    /**
     * 分页查询我的通知（按时间倒序，聚合触发人昵称头像）
     *
     * @param userId     当前用户ID
     * @param onlyUnread 是否仅看未读
     * @param pageQuery  分页参数
     * @return 通知分页结果
     */
    PageResult<NotificationVO> pageNotifications(Long userId, Boolean onlyUnread, BasePageQuery pageQuery);

    /**
     * 我的未读通知数（顶栏铃铛轮询）
     *
     * @param userId 当前用户ID
     * @return 未读数量
     */
    long unreadCount(Long userId);

    /**
     * 标记单条通知为已读（仅能操作本人的通知）
     *
     * @param userId 当前用户ID
     * @param id     通知ID
     * @return 是否成功
     */
    boolean markRead(Long userId, Long id);

    /**
     * 标记我的全部通知为已读
     *
     * @param userId 当前用户ID
     * @return 更新条数
     */
    int markAllRead(Long userId);
}
