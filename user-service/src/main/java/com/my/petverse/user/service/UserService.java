package com.my.petverse.user.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.user.UserLoginDTO;
import com.my.petverse.common.dto.user.UserRegisterDTO;
import com.my.petverse.common.dto.user.UserUpdateDTO;
import com.my.petverse.common.entity.user.User;
import com.my.petverse.common.vo.user.LoginVO;
import com.my.petverse.common.vo.user.UserVO;

import java.util.List;

/**
 * 用户服务接口
 */
public interface UserService extends IService<User> {

    /**
     * 用户注册，用户名重复抛业务异常
     *
     * @param dto 注册参数
     * @return 登录结果（自动签发令牌，注册后免二次登录）
     */
    LoginVO register(UserRegisterDTO dto);

    /**
     * 用户登录，校验密码并签发令牌
     *
     * @param dto 登录参数
     * @return 登录结果
     */
    LoginVO login(UserLoginDTO dto);

    /**
     * 根据用户ID查询用户信息
     *
     * @param id 用户ID
     * @return 用户信息，不存在返回 null
     */
    UserVO getUserById(Long id);

    /**
     * 按用户名或昵称模糊搜索用户（加好友场景），排除搜索者本人，最多返回 10 条
     *
     * @param keyword 搜索关键词
     * @param excludeUserId 需要排除的用户ID（当前登录用户）
     * @return 匹配的用户列表
     */
    List<UserVO> searchUsers(String keyword, Long excludeUserId);

    /**
     * 修改用户资料（昵称/头像），或校验原密码后修改密码
     *
     * @param dto 修改参数
     * @return 是否成功
     */
    boolean updateUser(UserUpdateDTO dto);
}
