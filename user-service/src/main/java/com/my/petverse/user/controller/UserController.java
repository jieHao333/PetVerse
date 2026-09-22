package com.my.petverse.user.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.user.UserLoginDTO;
import com.my.petverse.common.dto.user.UserRegisterDTO;
import com.my.petverse.common.dto.user.UserUpdateDTO;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.user.LoginVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.user.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 用户接口控制器
 */
@RestController
@RequestMapping("/user")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    /** 用户注册，注册成功直接返回登录结果 */
    @PostMapping("/register")
    public Result<LoginVO> register(@RequestBody @Valid UserRegisterDTO dto) {
        return Result.success(userService.register(dto));
    }

    /** 用户登录 */
    @PostMapping("/login")
    public Result<LoginVO> login(@RequestBody @Valid UserLoginDTO dto) {
        return Result.success(userService.login(dto));
    }

    /** 当前登录用户信息（用户ID取自登录令牌） */
    @GetMapping("/me")
    public Result<UserVO> me() {
        return Result.success(userService.getUserById(UserContext.getUserId()));
    }

    /** 根据ID查询用户信息 */
    @GetMapping("/{id}")
    public Result<UserVO> getById(@PathVariable("id") Long id) {
        return Result.success(userService.getUserById(id));
    }

    /** 按用户名或昵称搜索用户（加好友场景），排除当前登录用户 */
    @GetMapping("/search")
    public Result<List<UserVO>> search(@RequestParam("keyword") String keyword) {
        return Result.success(userService.searchUsers(keyword, UserContext.getUserId()));
    }

    /** 修改用户资料（昵称/头像/密码） */
    @PutMapping
    public Result<Boolean> update(@RequestBody @Valid UserUpdateDTO dto) {
        // 服务端以令牌中的用户ID为准，不允许修改他人资料
        dto.setId(UserContext.getUserId());
        return Result.success(userService.updateUser(dto));
    }

    /** 上传用户头像（存储到阿里云OSS），返回最新用户信息 */
    @PostMapping("/avatar")
    public Result<UserVO> uploadAvatar(@RequestParam("file") MultipartFile file) {
        return Result.success(userService.uploadAvatar(UserContext.getUserId(), file));
    }

    /** 升级用户角色（内部接口，仅供 Feign 调用，网关已拦截外部 /internal 请求） */
    @PutMapping("/internal/role")
    public Result<Boolean> upgradeRole(@RequestParam("userId") Long userId,
                                       @RequestParam("role") String role) {
        return Result.success(userService.upgradeRole(userId, role));
    }

    /** 批量查询用户信息（内部接口，供各服务列表页聚合昵称头像，一次请求代替逐条调用） */
    @GetMapping("/internal/batch")
    public Result<List<UserVO>> listByIds(@RequestParam("ids") List<Long> ids) {
        return Result.success(userService.listUserByIds(ids));
    }
}
