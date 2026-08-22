package com.my.petverse.pet.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.my.petverse.common.entity.pet.PetCatalog;
import org.apache.ibatis.annotations.Mapper;

/**
 * 宠物图鉴数据访问接口
 */
@Mapper
public interface PetCatalogMapper extends BaseMapper<PetCatalog> {
}
