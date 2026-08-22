package com.my.petverse.user.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.user.UserLoginDTO;
import com.my.petverse.common.dto.user.UserRegisterDTO;
import com.my.petverse.common.dto.user.UserUpdateDTO;
import com.my.petverse.common.entity.user.User;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.util.JwtUtil;
import com.my.petverse.common.vo.user.LoginVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.user.mapper.UserMapper;
import com.my.petverse.user.service.UserService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 用户服务实现类
 */
@Service
@RequiredArgsConstructor
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements UserService {

    private final JwtUtil jwtUtil;

    /** 密码加密器（BCrypt） */
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    @Override
    public LoginVO register(UserRegisterDTO dto) {
        // 校验用户名唯一
        Long count = count(new LambdaQueryWrapper<User>().eq(User::getUsername, dto.getUsername()));
        if (count > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "用户名已被注册");
        }
        // 昵称缺省时使用用户名
        String nickname = StringUtils.hasText(dto.getNickname()) ? dto.getNickname() : dto.getUsername();

        User user = new User();
        user.setUsername(dto.getUsername());
        user.setPassword(passwordEncoder.encode(dto.getPassword()));
        user.setNickname(nickname);
        user.setStatus(1);
        save(user);
        return buildLoginResult(user);
    }

    @Override
    public LoginVO login(UserLoginDTO dto) {
        User user = getOne(new LambdaQueryWrapper<User>().eq(User::getUsername, dto.getUsername()));
        if (user == null || !passwordEncoder.matches(dto.getPassword(), user.getPassword())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "用户名或密码错误");
        }
        if (user.getStatus() == null || user.getStatus() != 1) {
            throw new BusinessException(ResultCode.FORBIDDEN, "账号已被禁用");
        }
        // 更新最近登录时间
        user.setLastLoginTime(LocalDateTime.now());
        updateById(user);
        return buildLoginResult(user);
    }

    @Override
    public UserVO getUserById(Long id) {
        User user = getById(id);
        return user == null ? null : toVO(user);
    }

    /** 按用户名或昵称模糊搜索，仅返回正常状态用户，排除本人，最多 10 条 */
    @Override
    public List<UserVO> searchUsers(String keyword, Long excludeUserId) {
        if (!StringUtils.hasText(keyword)) {
            return List.of();
        }
        String trimmed = keyword.trim();
        List<User> users = list(new LambdaQueryWrapper<User>()
                .eq(User::getStatus, 1)
                .ne(excludeUserId != null, User::getId, excludeUserId)
                .and(w -> w.like(User::getUsername, trimmed)
                        .or()
                        .like(User::getNickname, trimmed))
                .orderByDesc(User::getCreateTime)
                .last("limit 10"));
        return users.stream().map(this::toVO).collect(Collectors.toList());
    }

    @Override
    public boolean updateUser(UserUpdateDTO dto) {
        User user = getById(dto.getId());
        if (user == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "用户不存在");
        }
        // 修改资料字段
        if (StringUtils.hasText(dto.getNickname())) {
            user.setNickname(dto.getNickname());
        }
        if (StringUtils.hasText(dto.getAvatar())) {
            user.setAvatar(dto.getAvatar());
        }
        // 修改密码：必须校验原密码
        if (StringUtils.hasText(dto.getNewPassword())) {
            if (!StringUtils.hasText(dto.getOldPassword())
                    || !passwordEncoder.matches(dto.getOldPassword(), user.getPassword())) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "原密码错误");
            }
            user.setPassword(passwordEncoder.encode(dto.getNewPassword()));
        }
        return updateById(user);
    }

    /**
     * 组装登录结果：生成令牌 + 用户信息
     */
    private LoginVO buildLoginResult(User user) {
        LoginVO vo = new LoginVO();
        vo.setToken(jwtUtil.createToken(user.getId(), user.getUsername()));
        vo.setUser(toVO(user));
        return vo;
    }

    /** DO 转 VO，剔除敏感字段 */
    private UserVO toVO(User user) {
        UserVO vo = new UserVO();
        BeanUtils.copyProperties(user, vo);
        return vo;
    }
}
