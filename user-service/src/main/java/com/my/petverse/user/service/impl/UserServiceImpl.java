package com.my.petverse.user.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.user.UserLoginDTO;
import com.my.petverse.common.dto.user.UserRegisterDTO;
import com.my.petverse.common.dto.user.UserUpdateDTO;
import com.my.petverse.common.entity.user.User;
import com.my.petverse.common.enums.UserRole;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.oss.OssService;
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
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 用户服务实现类
 */
@Service
@RequiredArgsConstructor
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements UserService {

    private final JwtUtil jwtUtil;

    private final OssService ossService;

    /** 密码加密器（BCrypt） */
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    /** 头像大小上限：2MB */
    private static final long MAX_AVATAR_SIZE = 2 * 1024 * 1024;

    /** 允许的头像内容类型 */
    private static final Set<String> AVATAR_CONTENT_TYPES = Set.of(
            "image/png", "image/jpeg", "image/jpg", "image/webp");

    private static final DateTimeFormatter AVATAR_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");

    /** OSS存储路径中的日期目录格式 */
    private static final DateTimeFormatter MEDIA_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyy/MM/dd");

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
        user.setRole(UserRole.USER.name());
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

    /** 上传头像：校验图片 -> 存入 OSS -> 更新用户头像地址 */
    @Override
    public UserVO uploadAvatar(Long userId, MultipartFile file) {

        String extension = getExtension(file.getOriginalFilename());

        if (file == null || file.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "请选择要上传的图片");
        }
        if (file.getSize() > MAX_AVATAR_SIZE) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "头像图片不能超过 2MB");
        }
        String contentType = file.getContentType();
        if (contentType == null || !AVATAR_CONTENT_TYPES.contains(contentType.toLowerCase())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅支持 PNG / JPEG / WEBP 格式的图片");
        }
        User user = getById(userId);
        if (user == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "用户不存在");
        }
        // 按日期分目录 + UUID 文件名，避免重名覆盖
//        String objectKey = "avatar/" + LocalDate.now().format(AVATAR_DATE_FORMAT) + "/"
//                + UUID.randomUUID().toString().replace("-", "") + getSuffix(file.getOriginalFilename());
        String objectKey = "avatar/" + LocalDate.now().format(MEDIA_DATE_FORMAT) + "/"
                + UUID.randomUUID().toString().replace("-", "") + "." + extension;
        user.setAvatar(ossService.upload(objectKey, file));
        updateById(user);
        return toVO(user);
    }

    /** 取文件后缀（含点），无后缀返回空串 */
    private String getSuffix(String filename) {
        if (!StringUtils.hasText(filename)) {
            return "";
        }
        int index = filename.lastIndexOf('.');
        if (index < 0 || index == filename.length() - 1) {
            return "";
        }
        return filename.substring(index).toLowerCase();
    }

    /** 升级用户角色（内部接口，供 shop-service 商家入驻审批通过后调用） */
    @Override
    public boolean upgradeRole(Long userId, String role) {
        User user = getById(userId);
        if (user == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "用户不存在");
        }
        // 仅允许升级为合法角色，且禁止通过此接口提升为管理员
        UserRole target = UserRole.of(role);
        if (target == UserRole.ADMIN) {
            throw new BusinessException(ResultCode.FORBIDDEN, "不允许升级为管理员");
        }
        user.setRole(target.name());
        return updateById(user);
    }

    /**
     * 组装登录结果：生成令牌 + 用户信息
     */
    private LoginVO buildLoginResult(User user) {
        LoginVO vo = new LoginVO();
        // 存量用户 role 可能为空，缺省按普通用户签发
        String role = StringUtils.hasText(user.getRole()) ? user.getRole() : UserRole.USER.name();
        vo.setToken(jwtUtil.createToken(user.getId(), user.getUsername(), role));
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
