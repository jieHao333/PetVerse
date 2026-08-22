package com.my.petverse.pet.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.pet.Pet;
import org.apache.ibatis.annotations.Mapper;

/**
 * 宠物数据访问接口
 */
@Mapper
public interface PetMapper extends BaseMapper<Pet> {
}
