function wintime(index)

if get(index,'userdata')
   set(index,'checked','off');
        set(index,'userdata',0);
        siglab('rawcommand',hex2dec('1a0400'));
else
   set(index,'checked','on');
        set(index,'userdata',1);
        siglab('rawcommand',hex2dec('11a0400'));
end;

%end function


