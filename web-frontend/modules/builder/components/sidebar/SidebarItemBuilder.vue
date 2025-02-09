<template>
  <li class="tree__sub" :class="{ active: page._.selected }">
    <a
      class="tree__sub-link"
      :title="page.name"
      :href="resolvePageHref(builder, page)"
      @mousedown.prevent
      @click.prevent="selectPage(builder, page)"
    >
      <Editable
        ref="rename"
        :value="page.name"
        @change="renamePage(builder, page, $event)"
      ></Editable>
    </a>
    <a
      v-show="!builder._.loading"
      v-if="showOptions"
      class="tree__options"
      @click="$refs.context.toggle($event.currentTarget, 'bottom', 'right', 0)"
      @mousedown.stop
    >
      <i class="fas fa-ellipsis-v"></i>
    </a>
    <Context
      ref="context"
      :overflow-scroll="true"
      :max-height-if-outside-viewport="true"
    >
      <div class="context__menu-title">{{ page.name }} ({{ page.id }})</div>
      <ul class="context__menu">
        <li
          v-if="
            $hasPermission('builder.page.update', page, builder.workspace.id)
          "
        >
          <a @click="enableRename()">
            <i class="context__menu-icon fas fa-fw fa-pen"></i>
            {{ $t('action.rename') }}
          </a>
        </li>
        <li
          v-if="
            $hasPermission('builder.page.duplicate', page, builder.workspace.id)
          "
        >
          <a
            :class="{
              'context__menu-item--loading': duplicateLoading,
              disabled: deleteLoading || duplicateLoading,
            }"
            @click="duplicatePage()"
          >
            <i class="context__menu-icon fas fa-fw fa-copy"></i>
            {{ $t('action.duplicate') }}
          </a>
        </li>
        <li
          v-if="
            $hasPermission('builder.page.delete', page, builder.workspace.id)
          "
        >
          <a
            :class="{ 'context__menu-item--loading': deleteLoading }"
            @click="deletePage()"
          >
            <i class="context__menu-icon fas fa-fw fa-trash"></i>
            {{ $t('action.delete') }}
          </a>
        </li>
      </ul>
    </Context>
  </li>
</template>

<script>
import { notifyIf } from '@baserow/modules/core/utils/error'
import { mapGetters } from 'vuex'

export default {
  name: 'SidebarItemBuilder',
  props: {
    builder: {
      type: Object,
      required: true,
    },
    page: {
      type: Object,
      required: true,
    },
  },
  data() {
    return {
      deleteLoading: false,
      duplicateLoading: false,
    }
  },
  computed: {
    showOptions() {
      return (
        this.$hasPermission(
          'builder.page.run_export',
          this.page,
          this.builder.workspace.id
        ) ||
        this.$hasPermission(
          'builder.page.update',
          this.page,
          this.builder.workspace.id
        ) ||
        this.$hasPermission(
          'builder.page.duplicate',
          this.page,
          this.builder.workspace.id
        )
      )
    },
    ...mapGetters({ duplicateJob: 'page/getDuplicateJob' }),
  },
  watch: {
    'duplicateJob.state'(newState) {
      if (['finished', 'failed'].includes(newState)) {
        this.duplicateLoading = false
      }
    },
  },
  methods: {
    setLoading(builder, value) {
      this.$store.dispatch('application/setItemLoading', {
        application: builder,
        value,
      })
    },
    selectPage(builder, page) {
      this.setLoading(builder, true)
      this.$nuxt.$router.push(
        {
          name: 'builder-page',
          params: {
            builderId: builder.id,
            pageId: page.id,
          },
        },
        () => {
          this.setLoading(builder, false)
        },
        () => {
          this.setLoading(builder, false)
        }
      )
    },
    resolvePageHref(builder, page) {
      const props = this.$nuxt.$router.resolve({
        name: 'builder-page',
        params: {
          builderId: builder.id,
          pageId: page.id,
        },
      })

      return props.href
    },
    enableRename() {
      this.$refs.context.hide()
      this.$refs.rename.edit()
    },
    async renamePage(builder, page, event) {
      this.setLoading(builder, true)
      try {
        await this.$store.dispatch('page/update', {
          builder,
          page,
          values: {
            name: event.value,
          },
        })
      } catch (error) {
        this.$refs.rename.set(event.oldValue)
        notifyIf(error, 'page')
      }

      this.setLoading(builder, false)
    },
    async deletePage() {
      this.deleteLoading = true

      try {
        await this.$store.dispatch('page/delete', {
          builder: this.builder,
          page: this.page,
        })
      } catch (error) {
        notifyIf(error, 'page')
      }

      this.deleteLoading = false
    },
    async duplicatePage() {
      if (this.duplicateLoading) {
        return
      }

      this.duplicateLoading = true

      try {
        await this.$store.dispatch('page/duplicate', { page: this.page })
      } catch (error) {
        notifyIf(error, 'page')
      }

      this.$refs.context.hide()
    },
  },
}
</script>
