package com.my.petverse.pet.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetProfileUpdateDTO;
import com.my.petverse.common.dto.pet.PetRegisterDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSignInDTO;
import com.my.petverse.common.dto.pet.PetUpdateDTO;
import com.my.petverse.common.entity.pet.Pet;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.pet.PetSignInVO;
import com.my.petverse.common.vo.pet.PetVO;

import org.springframework.web.multipart.MultipartFile;

import java.util.List;

/**
 * 宠物服务接口
 */
public interface PetService extends IService<Pet> {

    /** 根据ID查询宠物 */
    PetVO getPetById(Long id);

    /** 查询宠物列表 */
    List<PetVO> listPets();

    /** 分页查询宠物 */
    PageResult<PetVO> pagePets(PetPageQueryDTO query);

    /** 新增宠物，返回宠物ID */
    Long savePet(PetSaveDTO dto);

    /** 修改宠物 */
    boolean updatePet(PetUpdateDTO dto);

    /** 删除宠物，仅允许删除本人宠物 */
    boolean deletePet(Long petId, Long userId);

    /** 查询当前用户的代表宠物：优先第一只虚拟宠物，无则第一只真实宠物 */
    PetVO getMyPet(Long userId);

    /** 查询当前用户的全部宠物 */
    List<PetVO> listMyPets(Long userId);

    /** 领取虚拟宠物（随机抽取或自选），支持领养多只 */
    PetVO claimPet(PetClaimDTO dto);

    /** 登记真实宠物（名称 + 种类 + 收养时间） */
    PetVO registerPet(PetRegisterDTO dto);

    /** 完善真实宠物档案（种类/品种/性别/生日/绝育） */
    PetVO updatePetProfile(PetProfileUpdateDTO dto);

    /** 修改宠物名称 */
    PetVO renamePet(PetRenameDTO dto);

    /** 上传宠物头像（真实/虚拟宠物均可），返回更新后的宠物信息 */
    PetVO uploadAvatar(Long petId, Long userId, MultipartFile file);

    /** 每日签到，为用户所有虚拟宠物发放经验 */
    PetSignInVO signIn(PetSignInDTO dto);

    /** 按来源为用户所有虚拟宠物发放经验值（如发布动态），无虚拟宠物时返回 null */
    PetExpGainVO grantExp(PetExpGrantDTO dto);
}
