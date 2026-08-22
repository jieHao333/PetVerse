package com.my.petverse.user.controller;

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

    /** 当前登录用户信息（用户ID由网关注入的 X-User-Id 请求头提供） */
    @GetMapping("/me")
    public Result<UserVO> me(@RequestHeader("X-User-Id") Long userId) {
        return Result.success(userService.getUserById(userId));
    }

    /** 根据ID查询用户信息 */
    @GetMapping("/{id}")
    public Result<UserVO> getById(@PathVariable("id") Long id) {
        return Result.success(userService.getUserById(id));
    }

    /** 按用户名或昵称搜索用户（加好友场景），排除当前登录用户 */
    @GetMapping("/search")
    public Result<List<UserVO>> search(@RequestHeader("X-User-Id") Long userId,
                                       @RequestParam("keyword") String keyword) {
        return Result.success(userService.searchUsers(keyword, userId));
    }

    /** 修改用户资料（昵称/头像/密码） */
    @PutMapping
    public Result<Boolean> update(@RequestHeader("X-User-Id") Long userId,
                                  @RequestBody @Valid UserUpdateDTO dto) {
        // 服务端以令牌中的用户ID为准，不允许修改他人资料
        dto.setId(userId);
        return Result.success(userService.updateUser(dto));
    }
}
